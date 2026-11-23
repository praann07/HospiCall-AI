"""Optional local-LLM router (Ollama). It only *proposes* language; it never touches the database.

One JSON-constrained call returns {intent, specialty, reply}. Every field is validated against an
allow-list and the reply is passed through ``safety.guard_reply`` by the caller. If Ollama is down the
circuit breaker opens and the agent falls back to canned replies, so a dead model never breaks a call.
"""
import json

import httpx

from .resilience import BreakerOpen, CircuitBreaker

INTENTS = ["book", "cancel", "reschedule", "my_appointments", "doctor_list", "inquiry", "greeting",
           "goodbye", "out_of_scope", "unclear", "emergency", "self_harm"]

SYSTEM_PROMPT = """You are the phone receptionist of {hospital}. You only help with: booking, cancelling or
rescheduling appointments, listing doctors and departments, and hospital information (hours, location, parking,
fees, insurance). Classify the caller's last message and write a short, warm reply (max 2 sentences, plain
speech, no lists).
Rules: if the caller describes a medical emergency (someone not breathing, collapsed, seizure, poisoning, stroke or
heart-attack signs, severe bleeding) use intent "emergency"; if they talk about ending their own life or hurting
themselves use intent "self_harm". Never give medical advice, diagnoses, medicines or doses. Never promise that
something is booked or cancelled - the system does that. If the request is outside your scope, say you can only
help with hospital appointments and information. If you are unsure, use intent "unclear" and ask one
clarifying question.
Departments: {departments}."""


def _schema(departments: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["intent", "specialty", "reply"],
            "properties": {"intent": {"type": "string", "enum": INTENTS},
                           "specialty": {"type": ["string", "null"], "enum": [*departments, None]},
                           "reply": {"type": "string"}}}


class LLMClient:
    def __init__(self, base_url: str, model: str, hospital: str = "City Hospital", timeout: float = 20.0,
                 transport: httpx.BaseTransport | None = None, breaker: CircuitBreaker | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.hospital = hospital
        self.timeout = timeout
        self._transport = transport
        self.breaker = breaker or CircuitBreaker()

    def route(self, text: str, history: list[dict], departments: list[str]) -> dict | None:
        """Return a validated {intent, specialty, reply} dict, or None on any failure."""
        messages = [{"role": "system",
                     "content": SYSTEM_PROMPT.format(hospital=self.hospital, departments=", ".join(departments))}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history[-6:]
                     if m["role"] in ("user", "assistant")]
        messages.append({"role": "user", "content": text})
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "format": _schema(departments), "options": {"temperature": 0.2, "num_predict": 160}}

        def call() -> dict:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                return resp.json()

        try:
            body = self.breaker.call(call)
            data = json.loads(body["message"]["content"])
        except (httpx.HTTPError, BreakerOpen, KeyError, TypeError, ValueError):
            return None
        return self._validate(data, departments)

    @staticmethod
    def _validate(data: object, departments: list[str]) -> dict | None:
        if not isinstance(data, dict):
            return None
        intent, specialty, reply = data.get("intent"), data.get("specialty"), data.get("reply")
        if intent not in INTENTS or not isinstance(reply, str):
            return None
        return {"intent": intent, "specialty": specialty if specialty in departments else None, "reply": reply}

    def is_up(self) -> bool:
        try:
            with httpx.Client(timeout=1.5, transport=self._transport) as client:
                client.get(f"{self.base_url}/api/tags").raise_for_status()
            return True
        except httpx.HTTPError:
            return False
