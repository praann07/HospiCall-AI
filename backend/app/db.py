from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    call_id = Column(String, unique=True, index=True)
    phone = Column(String, index=True)
    outcome = Column(String, default="open")
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id = Column(Integer, primary_key=True)
    call_id = Column(String, index=True)
    speaker = Column(String)
    text = Column(Text)
    confidence = Column(Float, default=1.0)
    ts = Column(DateTime, default=datetime.utcnow)


class Intent(Base):
    __tablename__ = "intents"
    id = Column(Integer, primary_key=True)
    call_id = Column(String, index=True)
    intent_type = Column(String)
    slots_json = Column(Text)
    confidence = Column(Float, default=1.0)


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    specialization = Column(String, index=True)
    location = Column(String)
    rating = Column(Float, default=5.0)


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    user_phone = Column(String, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), index=True)
    slot_start = Column(DateTime)
    slot_end = Column(DateTime)
    status = Column(String, default="booked")


def init_db(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session
