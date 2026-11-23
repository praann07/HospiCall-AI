"""In-memory per-call state. Swap for Redis if the API ever runs on more than one worker."""
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MAX_TRANSCRIPT_TURNS = 40
MAX_SESSIONS = 5_000


@dataclass
class CallSession:
    call_id: str
    phone: str
    created_at: datetime
    last_activity: datetime
    transcript: list[dict] = field(default_factory=list)
    state: str | None = None          # dialogue state machine position (None = idle)
    ctx: dict = field(default_factory=dict)  # data the current state needs (offered slots, appointment ids, ...)
    verified: bool = False            # caller proved they own the phone number on file
    verify_attempts: int = 0
    name: str | None = None
    name_declined: bool = False       # caller chose not to give a name; do not ask again
    record: bool = True               # False once the caller opts out of recording
    last_intent: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


class SessionManager:
    """Sessions live in an LRU-ordered dict: the front is always the least recently used one, so expiry
    is O(expired) not O(all), and a hard cap (oldest evicted first) bounds memory under abuse."""

    def __init__(self, ttl_seconds: int = 900, clock: Callable[[], datetime] = datetime.now,
                 max_sessions: int = MAX_SESSIONS):
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, CallSession] = OrderedDict()

    def create(self, call_id: str, phone: str) -> CallSession:
        now = self._clock()
        session = CallSession(call_id=call_id, phone=phone, created_at=now, last_activity=now)
        with self._lock:
            self._cleanup(now)
            self._sessions[call_id] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
        return session

    def get(self, call_id: str) -> CallSession | None:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            session = self._sessions.get(call_id)
            if session:
                session.last_activity = now
                self._sessions.move_to_end(call_id)
            return session

    def add_turn(self, session: CallSession, role: str, content: str) -> None:
        session.transcript.append({"role": role, "content": content})
        del session.transcript[:-MAX_TRANSCRIPT_TURNS]

    def end(self, call_id: str) -> None:
        with self._lock:
            self._sessions.pop(call_id, None)

    def active(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _cleanup(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.ttl)
        while self._sessions:
            oldest = next(iter(self._sessions.values()))
            if oldest.last_activity >= cutoff:
                break
            self._sessions.popitem(last=False)
