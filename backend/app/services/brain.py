import re
import httpx

SPECIALTY_KEYWORDS = {
    "cardiology": ["cardio", "heart", "chest pain", "bp", "blood pressure"],
    "neurology": ["neuro", "brain", "headache", "migraine", "seizure", "memory"],
    "orthopedics": ["ortho", "bone", "joint", "knee", "spine", "back pain", "fracture"],
    "pediatrics": ["pediatr", "child", "baby", "kid"],
    "gynecology": ["gynec", "pregnancy", "pregnant"],
    "dermatology": ["derm", "skin", "rash"],
    "ophthalmology": ["eye", "vision", "ophthalm"],
    "ent": ["ear", "nose", "throat", "ent"],
}

TIMING_KEYWORDS = {
    "morning": ["morning"],
    "afternoon": ["afternoon"],
    "evening": ["evening"],
    "today": ["today"],
    "tomorrow": ["tomorrow"],
}

BOOKING_KEYWORDS = ["appointment", "book", "see a", "consult", "visit", "checkup", "meet", "take an", "fix up", "slot"]
CANCEL_KEYWORDS = ["cancel"]
RESCHEDULE_KEYWORDS = ["reschedule", "move the", "change the", "shift"]
EMERGENCY_KEYWORDS = ["emergency", "ambulance", "heart attack", "unconscious", "not breathing", "bleeding"]
INQUIRY_KEYWORDS = ["timing", "open", "hours", "location", "address", "reach", "parking", "fee", "cost", "charge", "where", "phone number", "contact"]
OUT_OF_SCOPE_KEYWORDS = ["medical advice", "symptom", "diagnos", "treatment for", "prescription", "should i take", "is it serious"]

SLOT_RE = re.compile(r"\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)\b")


def parse_slot(text: str) -> str | None:
    """Extract a slot label like '10 AM' from text ('at 10 am', 'book 2 PM')."""
    m = SLOT_RE.search(text.lower())
    if not m:
        return None
    hour, mer = int(m.group(1)), m.group(2).replace(".", "").lower()
    if hour < 1 or hour > 12:
        return None
    return f"{hour} {mer.upper()}"


class Brain:
    """LLM wrapper around Ollama (Llama 3.1 8B)."""

    def confirm_booking(self, text: str, pending: dict) -> str | None:
        """If a booking is pending, parse a chosen slot like '10 AM' from the text.
        Returns the slot label (e.g. '10 AM') or None if no slot chosen."""
        low = text.lower()
        slot = parse_slot(low)
        if not slot:
            return None
        if slot in pending.get("offered", []):
            return slot
        return None

    def fast_handle(self, text: str, find_slots: callable) -> tuple | None:
        """Instant keyword-based response. Returns (intent, response) or None if LLM needed."""
        low = text.lower()

        if any(k in low for k in EMERGENCY_KEYWORDS):
            return ("emergency", "Please call 108 immediately for an ambulance. A team member is being alerted right now.")
        if any(k in low for k in CANCEL_KEYWORDS):
            return ("cancel", "I can help you cancel an appointment. Could you tell me your registered phone number?")
        if any(k in low for k in RESCHEDULE_KEYWORDS):
            return ("reschedule", "I can help you reschedule. Could you tell me your registered phone number and the day you'd prefer?")
        if any(k in low for k in OUT_OF_SCOPE_KEYWORDS):
            return ("out_of_scope", "I can't give medical advice, but I can book you an appointment or connect you to a doctor.")

        specialty = next((s for s, kws in SPECIALTY_KEYWORDS.items() if any(k in low for k in kws)), None)
        timing = next((t for t, kws in TIMING_KEYWORDS.items() if any(k in low for k in kws)), "tomorrow")

        if any(k in low for k in BOOKING_KEYWORDS):
            slots = find_slots(specialty, timing)
            if slots:
                return ("book", f"Sure. Our {specialty or 'consultation'} department has openings {timing} at "
                                f"{', '.join(slots)}. Which one works best for you?")
            return ("book", f"I can book you a {specialty or 'consultation'} appointment. Could you tell me the preferred day and time?")

        if any(k in low for k in INQUIRY_KEYWORDS):
            return ("inquiry", "Our outpatient department is open Monday to Saturday, 8 AM to 8 PM, and 24/7 for emergencies. We're located at 100 Health Avenue, opposite the city park.")

        return None

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _request(self, system: str, user: str, temperature: float = 0.3) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()

    def classify_intent(self, text: str) -> str:
        system = (
            "You are an intent classifier for a hospital call assistant. "
            'Respond with ONLY one label: book, cancel, reschedule, inquiry, emergency, human, out_of_scope.'
        )
        return self._request(system, text, temperature=0.0)

    def generate_response(self, transcript: list[dict], slots: dict) -> str:
        system = (
            "You are the AI receptionist for City Hospital. "
            "You can ONLY schedule appointments, check availability, and answer general hospital questions. "
            "NEVER give medical advice, diagnoses, or prescriptions. "
            "If asked for medical advice, say: 'I can't give medical advice, but I can book you an appointment or connect you to a doctor.' "
            "Keep responses short, warm, and under 2 sentences when possible."
        )
        context = "\n".join(f"{t['role']}: {t['content']}" for t in transcript[-10:])
        user = f"Known info: {slots}\nConversation so far:\n{context}\n\nRespond as the receptionist."
        return self._request(system, user)
