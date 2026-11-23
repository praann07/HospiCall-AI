"""SQLite schema and engine setup.

Integrity rules live in the database, not just in Python:
* one *booked* appointment per doctor per start time (partial unique index), so two
  concurrent callers can never double-book a slot;
* cancelled slots become bookable again because the index only covers ``status = 'booked'``.
"""
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def _utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no tz type); avoids the deprecated datetime.utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


BOOKED = "booked"
CANCELLED = "cancelled"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String, index=True, nullable=True)  # masked; None once opted out
    outcome: Mapped[str] = mapped_column(String, default="open")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String, index=True)
    speaker: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Intent(Base):
    __tablename__ = "intents"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[str] = mapped_column(String, index=True)
    intent_type: Mapped[str] = mapped_column(String)
    slots_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    path: Mapped[str] = mapped_column(String, default="rules")  # rules | state | llm | fallback
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    specialization: Mapped[str] = mapped_column(String, index=True)
    location: Mapped[str] = mapped_column(String)
    rating: Mapped[float] = mapped_column(Float, default=5.0)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_phone: Mapped[str] = mapped_column(String, index=True)
    patient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    slot_start: Mapped[datetime] = mapped_column(DateTime)
    slot_end: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, default=BOOKED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("uq_doctor_slot_booked", "doctor_id", "slot_start", unique=True,
              sqlite_where=(status == BOOKED)),
        Index("uq_patient_slot_booked", "user_phone", "slot_start", unique=True,
              sqlite_where=(status == BOOKED)),
    )


class AuditEvent(Base):
    """Append-only trail of sensitive actions. The application never updates or deletes rows."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    call_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    action: Mapped[str] = mapped_column(String, index=True)
    detail: Mapped[str] = mapped_column(Text, default="")


def init_db(db_path: str) -> sessionmaker:
    """Create the engine (WAL, busy timeout, FK enforcement), the tables, and return a session factory."""
    if db_path == ":memory:":
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
