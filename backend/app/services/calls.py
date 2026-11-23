"""Call lifecycle: start a call, run turns through the agent, persist what the caller allowed us to keep."""
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from ..db import Conversation, Intent, TranscriptSegment
from . import nlu
from .agent import Agent, Reply
from .audit import mask_phone
from .metrics import Metrics
from .session import CallSession, SessionManager

_PHONE_LIKE = re.compile(r"\d(?:[\s.-]?\d){6,}")  # 7+ digits in a row: a phone number, never kept
SPOKEN_PHONE_DIGITS = 7


def redact(text: str, verifying: bool) -> str:
    """What may be stored of a caller's words: no verification digits, no phone numbers (typed or spoken)."""
    spoken = len(nlu.parse_digits(text))
    if (verifying and spoken) or spoken >= SPOKEN_PHONE_DIGITS:
        return "[digits withheld]"
    return _PHONE_LIKE.sub("#", text)


class UnknownCall(Exception):
    """The call id has no live session (never started, ended, or expired)."""


@dataclass
class TurnResult:
    reply: Reply
    latency_ms: float


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CallService:
    def __init__(self, agent: Agent, sessions: SessionManager, session_factory: sessionmaker, metrics: Metrics):
        self.agent = agent
        self.sessions = sessions
        self._Session = session_factory
        self.metrics = metrics

    def start(self, phone: str) -> tuple[CallSession, str]:
        session = self.sessions.create(uuid.uuid4().hex, phone)
        greeting = self.agent.opening()  # generic: nothing about the caller is revealed before verification
        with self._Session() as db:
            db.add(Conversation(call_id=session.call_id, phone=mask_phone(phone)))
            db.commit()
        self._save(session, "agent", greeting)
        self.sessions.add_turn(session, "assistant", greeting)
        self.metrics.inc("hospicall_calls_started_total")
        return session, greeting

    def turn(self, call_id: str, text: str) -> TurnResult:
        session = self._session(call_id)
        with session.lock:  # two overlapping requests for one call must not interleave state changes
            return self._turn(session, text)

    def _turn(self, session: CallSession, text: str) -> TurnResult:
        call_id = session.call_id
        stored = redact(text, verifying=session.state == "verify")
        started = time.perf_counter()
        was_recording = session.record
        reply = self.agent.handle(session, text)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        self.sessions.add_turn(session, "user", stored)
        self.sessions.add_turn(session, "assistant", reply.text)
        if was_recording and not session.record:
            self._erase_transcript(call_id)
        self._save(session, "caller", stored)
        self._save(session, "agent", reply.text, reply.confidence)
        self._save_intent(call_id, reply, latency_ms)
        self.metrics.observe_turn(reply.path, reply.intent, latency_ms)
        if reply.outcome:
            self.metrics.inc("hospicall_outcomes_total", outcome=reply.outcome)
            self._set_outcome(call_id, reply.outcome)
        if reply.end_call:
            self.end(call_id)
        return TurnResult(reply, latency_ms)

    def end(self, call_id: str) -> bool:
        known = self.sessions.get(call_id) is not None
        self.sessions.end(call_id)
        with self._Session() as db:
            conv = db.scalar(select(Conversation).where(Conversation.call_id == call_id))
            if conv and conv.ended_at is None:
                conv.ended_at = _utcnow()
                if conv.outcome == "open":
                    conv.outcome = "completed"
                db.commit()
        return known

    def transcript(self, call_id: str) -> list[dict] | None:
        with self._Session() as db:
            if db.scalar(select(Conversation.id).where(Conversation.call_id == call_id)) is None:
                return None
            rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.call_id == call_id)
                              .order_by(TranscriptSegment.id))
            return [{"speaker": r.speaker, "text": r.text, "ts": r.ts.isoformat()} for r in rows]

    # ---- internals -------------------------------------------------------------------------------
    def _session(self, call_id: str) -> CallSession:
        session = self.sessions.get(call_id)
        if session is None:
            raise UnknownCall(call_id)
        return session

    def _save(self, session: CallSession, speaker: str, text: str, confidence: float = 1.0) -> None:
        if not session.record:
            return
        with self._Session() as db:
            db.add(TranscriptSegment(call_id=session.call_id, speaker=speaker, text=text, confidence=confidence))
            db.commit()

    def _erase_transcript(self, call_id: str) -> None:
        with self._Session() as db:
            db.execute(delete(TranscriptSegment).where(TranscriptSegment.call_id == call_id))
            conv = db.scalar(select(Conversation).where(Conversation.call_id == call_id))
            if conv:
                conv.phone = None  # an opted-out call keeps no link back to the caller
            db.commit()

    def _save_intent(self, call_id: str, reply: Reply, latency_ms: float) -> None:
        with self._Session() as db:
            db.add(Intent(call_id=call_id, intent_type=reply.intent, slots_json=json.dumps(reply.slots, default=str),
                          confidence=reply.confidence, path=reply.path, latency_ms=latency_ms))
            db.commit()

    def _set_outcome(self, call_id: str, outcome: str) -> None:
        with self._Session() as db:
            conv = db.scalar(select(Conversation).where(Conversation.call_id == call_id))
            if conv:
                conv.outcome = outcome
                db.commit()
