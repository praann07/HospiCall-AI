from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timedelta
import os, tempfile
from .config import settings
from .db import init_db, User, Conversation, TranscriptSegment, Intent, Doctor, Appointment
from .services.brain import Brain, parse_slot
from .services.voice import Voice
from .services.session import SessionManager

app = FastAPI(title="HospiCall")
Session = init_db(settings.db_path)
brain = Brain(settings.ollama_url, settings.llm_model)
voice = Voice(settings.omnivoice_url, settings.tts_engine, settings.stt_engine)
sessions = SessionManager(settings.session_ttl_seconds)

TMP_AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tmp")
os.makedirs(TMP_AUDIO_DIR, exist_ok=True)


class CallEvent(BaseModel):
    call_id: str
    phone: str
    audio_path: str = None


class ChatMessage(BaseModel):
    call_id: str
    text: str


class SynthesizeRequest(BaseModel):
    text: str


def find_slots(specialty: str | None, timing: str) -> list[str]:
    """Next available time slots for a specialty on the preferred day. Instant, no LLM."""
    if not specialty:
        return []
    day = _target_day(timing)
    booked = set()
    with Session() as db:
        doc = db.query(Doctor).filter(Doctor.specialization.ilike(specialty)).first()
        if not doc:
            return []
        for a in _appt_between(db.query(Appointment), doc.id, datetime.combine(day, datetime.min.time()),
                               datetime.combine(day + timedelta(days=1), datetime.min.time())).all():
            booked.add(a.slot_start.strftime("%I %p").lstrip("0"))
    window = _day_window(timing)
    return [s for s in window if _slot_time(s).strftime("%I %p").lstrip("0") not in booked][:3]


def _target_day(timing: str) -> datetime.date:
    d = datetime.now().date() + timedelta(days=1 if timing == "tomorrow" else 0)
    if timing == "today":
        d = datetime.now().date()
    return d


def _day_window(timing: str) -> list[str]:
    if timing in ("tomorrow", "today"):
        return ["9 AM", "10 AM", "11 AM", "2 PM", "3 PM", "4 PM"]
    return {
        "morning": ["9 AM", "10 AM", "11 AM"],
        "afternoon": ["2 PM", "3 PM"],
        "evening": ["4 PM", "5 PM"],
    }.get(timing, ["9 AM", "10 AM", "11 AM", "2 PM", "3 PM", "4 PM"])


def _slot_time(label: str) -> datetime:
    """Convert '10 AM' to a datetime time for comparison."""
    hh, mer = label.split()
    hh = int(hh)
    if mer == "PM" and hh != 12:
        hh += 12
    if mer == "AM" and hh == 12:
        hh = 0
    return datetime(2000, 1, 1, hh, 0)


def _slot_to_datetime(day: datetime.date, label: str) -> datetime:
    t = _slot_time(label)
    return datetime.combine(day, t.time())


def _appt_between(q, doc_id, start, end):
    return q.filter(
        Appointment.doctor_id == doc_id,
        Appointment.slot_start >= start,
        Appointment.slot_start < end,
    )


def process_text(call_id: str, text: str) -> dict:
    """Shared brain: handles booking confirmation, fast-path, and LLM fallback."""
    pending = sessions.get_pending_booking(call_id)
    if pending:
        chosen = brain.confirm_booking(text, pending)
        if chosen:
            response = _confirm_and_book(call_id, pending, chosen)
            sessions.clear_pending_booking(call_id)
            intent = "booked"
        else:
            intent, response = _fallback_brain(call_id, text)
    else:
        fast = brain.fast_handle(text, find_slots)
        if fast:
            intent, response = fast
            if intent == "book":
                pending = _capture_pending(text)
                if pending.get("specialty"):
                    sessions.set_pending_booking(call_id, pending)
        else:
            intent, response = _fallback_brain(call_id, text)

    sessions.add_turn(call_id, "patient", text)
    sessions.update_slots(call_id, intent=intent)
    with Session() as db:
        db.add(Intent(call_id=call_id, intent_type=intent, slots_json="{}"))
        db.add(TranscriptSegment(call_id=call_id, speaker="patient", text=text))
        db.add(TranscriptSegment(call_id=call_id, speaker="ai", text=response))
        db.commit()
    sessions.add_turn(call_id, "ai", response)
    return {"intent": intent, "response": response}


def _fallback_brain(call_id: str, text: str):
    intent = brain.classify_intent(text)
    if intent == "emergency":
        return ("emergency", "Please call 108 immediately for an ambulance. Hold the line and a team member will assist you.")
    if intent == "out_of_scope":
        return ("out_of_scope", "I can't give medical advice, but I can book you an appointment or connect you to a doctor.")
    s = sessions.get(call_id)
    response = brain.generate_response(s["transcript"], s["slots"])
    return (intent, response)


def _capture_pending(text: str) -> dict:
    low = text.lower()
    from .services.brain import SPECIALTY_KEYWORDS, TIMING_KEYWORDS
    specialty = next((s for s, kws in SPECIALTY_KEYWORDS.items() if any(k in low for k in kws)), None)
    timing = next((t for t, kws in TIMING_KEYWORDS.items() if any(k in low for k in kws)), "tomorrow")
    return {"specialty": specialty, "timing": timing, "offered": find_slots(specialty, timing)}


def _confirm_and_book(call_id: str, pending: dict, chosen: str) -> str:
    specialty, timing = pending.get("specialty"), pending.get("timing", "tomorrow")
    day = _target_day(timing)
    slot_dt = _slot_to_datetime(day, chosen)
    with Session() as db:
        doc = db.query(Doctor).filter(Doctor.specialization.ilike(specialty)).first()
        if not doc:
            return "I couldn't find that department. Could you try another one?"
        conflict = db.query(Appointment).filter(
            Appointment.doctor_id == doc.id, Appointment.slot_start == slot_dt).first()
        if conflict:
            fresh = find_slots(specialty, timing)
            return (f"Sorry, {chosen} just got taken. {specialty.capitalize()} is available "
                    f"at: {', '.join(fresh) or 'no other time today'}. Which works?")
        s = sessions.get(call_id)
        db.add(Appointment(user_phone=s["phone"], doctor_id=doc.id,
                           slot_start=slot_dt, slot_end=slot_dt + timedelta(minutes=30)))
        db.commit()
        when = _when_brief(timing)
        return f"Booked, {doc.name}, {specialty} {when} at {chosen}. Anything else?"


def _when_brief(timing: str) -> str:
    return {"today": "today", "tomorrow": "tomorrow", "morning": "tomorrow morning",
            "afternoon": "tomorrow afternoon", "evening": "tomorrow evening"}.get(timing, "tomorrow")


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
    return process_text(msg.call_id, msg.text)


@app.post("/calls/audio")
async def audio(call_id: str = ..., phone: str = "demo", file: UploadFile = File(...)):
    """STT an uploaded mic recording, run the brain, return text."""
    data = await file.read()
    suffix = ".wav" if file.content_type == "audio/wav" else ".webm"
    path = os.path.join(TMP_AUDIO_DIR, f"{call_id}_{datetime.now().strftime('%H%M%S%f')}{suffix}")
    with open(path, "wb") as f:
        f.write(data)
    sessions.create(call_id, phone)
    text = voice.transcribe(path)
    result = process_text(call_id, text)
    result["call_id"] = call_id
    result["user_text"] = text
    return result


@app.post("/synthesize")
def synthesize(req: SynthesizeRequest):
    path = os.path.join(TMP_AUDIO_DIR, f"tts_{datetime.now().strftime('%H%M%S%f')}.wav")
    voice.synthesize(req.text, settings.tts_voice, path)
    return FileResponse(path, media_type="audio/wav")


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


@app.get("/calls/{call_id}/pending")
def get_pending(call_id: str):
    return {"pending": sessions.get_pending_booking(call_id)}


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")