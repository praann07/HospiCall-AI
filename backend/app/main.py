from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
from .config import settings
from .db import init_db, User, Conversation, TranscriptSegment, Intent, Doctor, Appointment
from .services.brain import Brain
from .services.voice import Voice
from .services.session import SessionManager

app = FastAPI(title="HospiCall")
Session = init_db(settings.db_path)
brain = Brain(settings.ollama_url, settings.llm_model)
voice = Voice(settings.omnivoice_url, settings.tts_engine, settings.stt_engine)
sessions = SessionManager(settings.session_ttl_seconds)


class CallEvent(BaseModel):
    call_id: str
    phone: str
    audio_path: str = None


class ChatMessage(BaseModel):
    call_id: str
    text: str


def find_slots(specialty: str | None, timing: str) -> list[str]:
    """Next available time slots for a specialty on the preferred day. Instant, no LLM."""
    if not specialty:
        return []
    day = datetime.now().date() + timedelta(days=1 if timing == "tomorrow" else 0)
    if timing == "today":
        day = datetime.now().date()
    with Session() as db:
        doc = db.query(Doctor).filter(Doctor.specialization.ilike(specialty)).first()
        if not doc:
            return []
        booked = {a.slot_start.time().strftime("%I %p").lstrip("0") for a in
                  db.query(Appointment).filter(
                      Appointment.doctor_id == doc.id,
                      Appointment.slot_start >= datetime.combine(day, datetime.min.time()),
                      Appointment.slot_start < datetime.combine(day + timedelta(days=1), datetime.min.time()),
                  ).all()}
    window = ["9 AM", "10 AM", "11 AM", "2 PM", "3 PM", "4 PM"] if timing in ("tomorrow", "today") else {
        "morning": ["9 AM", "10 AM", "11 AM"],
        "afternoon": ["2 PM", "3 PM"],
        "evening": ["4 PM", "5 PM"],
    }.get(timing, ["9 AM", "10 AM", "11 AM", "2 PM", "3 PM", "4 PM"])
    return [s for s in window if s not in booked][:3]


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.post("/calls/start")
def start_call(event: CallEvent):
    s = sessions.create(event.call_id, event.phone)
    with Session() as db:
        db.add(Conversation(call_id=event.call_id, phone=event.phone))
        db.commit()
    return {"call_id": event.call_id, "greeting": "Welcome to City Hospital. How can I help you?"}


@app.post("/calls/transcribe")
def transcribe(event: CallEvent):
    text = voice.transcribe(event.audio_path)
    with Session() as db:
        db.add(TranscriptSegment(call_id=event.call_id, speaker="patient", text=text))
        db.commit()
    return {"text": text}


@app.post("/calls/message")
def message(msg: ChatMessage):
    fast = brain.fast_handle(msg.text, find_slots)
    if fast:
        intent, response = fast
    else:
        intent = brain.classify_intent(msg.text)
        sessions.update_slots(msg.call_id, intent=intent)

        if intent == "emergency":
            response = "Please call 108 immediately for an ambulance. Hold the line and a team member will assist you."
        elif intent == "out_of_scope":
            response = "I can't give medical advice, but I can book you an appointment or connect you to a doctor."
        else:
            s = sessions.get(msg.call_id)
            response = brain.generate_response(s["transcript"], s["slots"])

    sessions.add_turn(msg.call_id, "patient", msg.text)
    sessions.update_slots(msg.call_id, intent=intent)

    with Session() as db:
        db.add(Intent(call_id=msg.call_id, intent_type=intent, slots_json="{}"))
        db.add(TranscriptSegment(call_id=msg.call_id, speaker="patient", text=msg.text))
        db.commit()

    sessions.add_turn(msg.call_id, "ai", response)
    with Session() as db:
        db.add(TranscriptSegment(call_id=msg.call_id, speaker="ai", text=response))
        db.commit()
    return {"intent": intent, "response": response}


@app.post("/calls/{call_id}/end")
def end_call(call_id: str):
    with Session() as db:
        conv = db.query(Conversation).filter_by(call_id=call_id).first()
        if conv:
            conv.outcome = "completed"
            conv.ended_at = __import__("datetime").datetime.utcnow()
            db.commit()
    sessions.end(call_id)
    return {"status": "ended"}


@app.get("/calls/{call_id}/transcript")
def get_transcript(call_id: str):
    with Session() as db:
        segs = db.query(TranscriptSegment).filter_by(call_id=call_id).all()
        return [{"speaker": s.speaker, "text": s.text} for s in segs]
