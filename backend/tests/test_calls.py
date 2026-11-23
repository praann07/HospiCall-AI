"""CallService: persistence, outcomes, privacy (redaction, opt-out), session expiry."""
import pytest
from sqlalchemy import select

from app.db import Conversation, Intent
from app.services.audit import mask_phone
from app.services.calls import UnknownCall, redact

from .conftest import PHONE


def conversation(session_factory, call_id):
    with session_factory() as db:
        return db.scalar(select(Conversation).where(Conversation.call_id == call_id))


def test_start_persists_conversation_and_greeting(caller, calls, session_factory):
    conv = conversation(session_factory, caller.call_id)
    assert conv.phone == mask_phone(PHONE) and conv.outcome == "open" and conv.ended_at is None
    segments = calls.transcript(caller.call_id)
    assert segments[0]["speaker"] == "agent" and "automated AI assistant" in segments[0]["text"]


def test_turns_are_transcribed_in_order(caller, calls):
    caller.run("hello", "what are your hours")
    speakers = [s["speaker"] for s in calls.transcript(caller.call_id)]
    assert speakers == ["agent", "caller", "agent", "caller", "agent"]


def test_intent_rows_record_path_and_latency(caller, session_factory):
    caller.say("what are your hours")
    with session_factory() as db:
        row = db.scalars(select(Intent).where(Intent.call_id == caller.call_id)).one()
    assert row.intent_type == "inquiry" and row.path == "rules" and row.latency_ms >= 0


def test_booking_sets_conversation_outcome(caller, session_factory):
    caller.run("book a neurologist tomorrow at 10 am", "skip", "yes")
    assert conversation(session_factory, caller.call_id).outcome == "booked"


def test_ending_a_call_stamps_end_time_and_completes_it(caller, calls, session_factory):
    caller.say("what are your hours")
    assert calls.end(caller.call_id) is True
    conv = conversation(session_factory, caller.call_id)
    assert conv.ended_at is not None and conv.outcome == "completed"
    assert calls.end(caller.call_id) is False  # already gone


def test_goodbye_ends_the_call_automatically(caller, calls):
    caller.say("bye")
    with pytest.raises(UnknownCall):
        calls.turn(caller.call_id, "hello again")


def test_turn_on_unknown_call_raises(calls):
    with pytest.raises(UnknownCall):
        calls.turn("does-not-exist", "hello")


def test_session_expires_after_ttl(caller, calls, clock):
    clock.advance(minutes=16)
    with pytest.raises(UnknownCall):
        calls.turn(caller.call_id, "hello")
    assert calls.transcript(caller.call_id) is not None  # the saved record outlives the live session


def test_activity_keeps_session_alive(caller, calls, clock):
    for _ in range(3):
        clock.advance(minutes=10)
        caller.say("hello")  # each turn refreshes the TTL
    assert calls.sessions.active() == 1


def test_transcript_of_unknown_call_is_none(calls):
    assert calls.transcript("nope") is None


def test_verification_digits_are_masked_in_storage(caller, calls):
    caller.run("cancel my appointment", "my code is 3210")
    stored = " ".join(s["text"] for s in calls.transcript(caller.call_id))
    assert "3210" not in stored and "[digits withheld]" in stored


def test_opt_out_before_any_message_saves_nothing_more(caller, calls):
    caller.say("don't record this call")
    caller.run("what are your hours", "where are you")
    assert calls.transcript(caller.call_id) == []


def test_metrics_count_turns_and_outcomes(caller, metrics):
    caller.run("book a neurologist tomorrow at 10 am", "skip", "yes")
    snap = metrics.snapshot()
    names = {c["name"] for c in snap["counters"]}
    assert {"hospicall_calls_started_total", "hospicall_turns_total", "hospicall_outcomes_total"} <= names
    assert snap["latency"]["rules"]["count"] == 3
    assert "hospicall_turns_total" in metrics.prometheus()


# ---------------------------------------------------------------------------------------------- privacy extras
def test_conversation_stores_only_a_masked_phone(caller, session_factory):
    assert conversation(session_factory, caller.call_id).phone == "******3210"


def test_opt_out_unlinks_the_conversation_from_the_caller(caller, session_factory):
    caller.say("don't record this call")
    assert conversation(session_factory, caller.call_id).phone is None


@pytest.mark.parametrize("text, verifying, expected", [
    ("my code is 3210", True, "[digits withheld]"),
    ("three two one zero", True, "[digits withheld]"),
    ("9876543210", False, "[digits withheld]"),
    ("nine eight seven six five four three two one zero", False, "[digits withheld]"),
    ("call 98765 43210 please", False, "[digits withheld]"),
    ("book tomorrow at 10", False, "book tomorrow at 10"),
    ("it is 3210", False, "it is 3210"),   # four digits outside verification are not a phone number
])
def test_redact(text, verifying, expected):
    assert redact(text, verifying) == expected

