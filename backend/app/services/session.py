from datetime import datetime, timedelta


class SessionManager:
    """In-memory session state per call. Redis replaces this in production."""

    def __init__(self, ttl_seconds: int = 900):
        self._sessions = {}
        self.ttl = ttl_seconds

    def create(self, call_id: str, phone: str) -> dict:
        self._cleanup()
        session = {
            "call_id": call_id,
            "phone": phone,
            "transcript": [],
            "slots": {},
            "created_at": datetime.now().isoformat(),
            "last_activity": datetime.now(),
        }
        self._sessions[call_id] = session
        return session

    def get(self, call_id: str) -> dict | None:
        self._cleanup()
        s = self._sessions.get(call_id)
        if s:
            s["last_activity"] = datetime.now()
        return s

    def add_turn(self, call_id: str, role: str, content: str):
        s = self.get(call_id)
        if s:
            s["transcript"].append({"role": role, "content": content})

    def update_slots(self, call_id: str, **slots):
        s = self.get(call_id)
        if s:
            s["slots"].update(slots)

    def end(self, call_id: str):
        self._sessions.pop(call_id, None)

    def _cleanup(self):
        cutoff = datetime.now() - timedelta(seconds=self.ttl)
        stale = [k for k, v in self._sessions.items() if v["last_activity"] < cutoff]
        for k in stale:
            self._sessions.pop(k, None)
