"""SessionManager: bounded memory (LRU cap), idle expiry, and per-call locking."""
from app.services.session import MAX_TRANSCRIPT_TURNS, SessionManager


def test_oldest_sessions_are_evicted_past_the_cap(clock):
    sessions = SessionManager(900, clock, max_sessions=3)
    for i in range(5):
        sessions.create(f"call-{i}", "9876543210")
    assert sessions.active() == 3
    assert sessions.get("call-0") is None and sessions.get("call-1") is None
    assert sessions.get("call-4") is not None


def test_touching_a_session_protects_it_from_eviction(clock):
    sessions = SessionManager(900, clock, max_sessions=3)
    for i in range(3):
        sessions.create(f"call-{i}", "9876543210")
    assert sessions.get("call-0") is not None  # now the most recently used
    sessions.create("call-3", "9876543210")  # evicts the least recently used: call-1
    assert sessions.get("call-1") is None
    assert sessions.get("call-0") is not None


def test_idle_sessions_expire_but_active_ones_do_not(clock):
    sessions = SessionManager(60, clock)
    sessions.create("idle", "9876543210")
    sessions.create("busy", "9876543210")
    clock.advance(seconds=45)
    assert sessions.get("busy") is not None  # refreshes busy only
    clock.advance(seconds=30)  # idle is 75 s old, busy only 30 s
    assert sessions.get("idle") is None
    assert sessions.get("busy") is not None


def test_expiry_cleans_up_on_create_so_memory_cannot_grow_unseen(clock):
    sessions = SessionManager(60, clock)
    for i in range(10):
        sessions.create(f"old-{i}", "9876543210")
    clock.advance(seconds=120)
    sessions.create("fresh", "9876543210")
    assert sessions.active() == 1


def test_end_is_idempotent(clock):
    sessions = SessionManager(900, clock)
    sessions.create("a", "9876543210")
    sessions.end("a")
    sessions.end("a")
    assert sessions.active() == 0


def test_transcript_keeps_only_the_most_recent_turns(clock):
    sessions = SessionManager(900, clock)
    session = sessions.create("a", "9876543210")
    for i in range(MAX_TRANSCRIPT_TURNS + 10):
        sessions.add_turn(session, "user", f"turn {i}")
    assert len(session.transcript) == MAX_TRANSCRIPT_TURNS
    assert session.transcript[-1]["content"] == f"turn {MAX_TRANSCRIPT_TURNS + 9}"


def test_each_session_has_its_own_lock(clock):
    sessions = SessionManager(900, clock)
    a, b = sessions.create("a", "9876543210"), sessions.create("b", "9876543210")
    assert a.lock is not b.lock
    with a.lock:
        assert b.lock.acquire(blocking=False)  # one slow call never blocks another
        b.lock.release()
