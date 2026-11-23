"""Append-only audit trail for sensitive actions. Phone numbers are masked before they are stored."""
import json

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..db import AuditEvent


def mask_phone(phone: str | None) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return ("*" * max(len(digits) - 4, 0) + digits[-4:]) if digits else "unknown"


class Audit:
    def __init__(self, session_factory: sessionmaker):
        self._Session = session_factory

    def log(self, call_id: str | None, action: str, **detail) -> None:
        with self._Session() as db:
            db.add(AuditEvent(call_id=call_id, action=action, detail=json.dumps(detail, default=str)))
            db.commit()

    def recent(self, limit: int = 100, call_id: str | None = None) -> list[dict]:
        stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
        if call_id:
            stmt = stmt.where(AuditEvent.call_id == call_id)
        with self._Session() as db:
            return [{"id": e.id, "ts": e.ts.isoformat(), "call_id": e.call_id, "action": e.action,
                     "detail": json.loads(e.detail or "{}")} for e in db.scalars(stmt)]
