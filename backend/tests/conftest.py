"""Shared fixtures. Time is frozen to Monday 2026-11-23 08:00 so slot maths is deterministic."""
from datetime import datetime, timedelta

import pytest

from app.config import Settings
from app.db import init_db
from app.seed import seed_if_empty
from app.services.agent import Agent
from app.services.audit import Audit
from app.services.calls import CallService
from app.services.metrics import Metrics
from app.services.scheduler import Scheduler
from app.services.session import SessionManager

PHONE = "9876543210"          # a patient with no appointments
DEMO_PHONE = "9441321662"     # the seeded demo patient


class Clock:
    """Mutable fake clock: call it for 'now', `.advance()` to move it."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 11, 23, 8, 0))  # a Monday


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, db_path=":memory:", rate_limit_per_minute=0, admin_api_key="test-key")


@pytest.fixture
def session_factory():
    factory = init_db(":memory:")
    seed_if_empty(factory)
    return factory


@pytest.fixture
def scheduler(session_factory, clock) -> Scheduler:
    return Scheduler(session_factory, clock)


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def calls(scheduler, settings, session_factory, clock, metrics) -> CallService:
    agent = Agent(scheduler, settings, None, Audit(session_factory))
    return CallService(agent, SessionManager(settings.session_ttl_seconds, clock), session_factory, metrics)


class Caller:
    """Drives one conversation through CallService and remembers the replies."""

    def __init__(self, calls: CallService, phone: str = PHONE):
        self.calls = calls
        self.session, self.greeting = calls.start(phone)
        self.call_id = self.session.call_id

    def say(self, text: str):
        return self.calls.turn(self.call_id, text).reply

    def run(self, *texts: str):
        return [self.say(t) for t in texts]


@pytest.fixture
def caller(calls) -> Caller:
    return Caller(calls)


@pytest.fixture
def make_caller(calls):
    return lambda phone=PHONE: Caller(calls, phone)
