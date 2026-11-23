"""Demo data: a small roster of doctors and one upcoming appointment for the demo patient.

Both functions are idempotent, so the API can call them on every start-up.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .db import BOOKED, Appointment, Doctor, User
from .services.scheduler import APPOINTMENT_MINUTES, CLOSED_WEEKDAY

DEMO_PHONE = "9441321662"
DEMO_NAME = "Praneeth"

DOCTORS = [  # (name, specialization, location, rating)
    ("Dr. Mehta", "Cardiology", "Block A, 2nd floor", 4.9),
    ("Dr. Rao", "Cardiology", "Block A, 2nd floor", 4.7),
    ("Dr. Iyer", "Neurology", "Block B, 1st floor", 4.8),
    ("Dr. Kapoor", "Orthopedics", "Block C, 1st floor", 4.6),
    ("Dr. Nair", "Pediatrics", "Block D, ground floor", 4.9),
    ("Dr. Sharma", "Gynecology", "Block D, 1st floor", 4.8),
    ("Dr. Reddy", "Dermatology", "Block B, ground floor", 4.7),
    ("Dr. Khan", "ENT", "Block C, ground floor", 4.6),
    ("Dr. Menon", "Ophthalmology", "Block B, 2nd floor", 4.7),
    ("Dr. Patel", "General Medicine", "Block A, ground floor", 4.5),
    ("Dr. Das", "General Medicine", "Block A, ground floor", 4.4),
]


def seed_if_empty(session_factory: sessionmaker) -> bool:
    """Insert the doctor roster when the table is empty. Returns True if anything was inserted."""
    with session_factory() as db:
        if db.scalar(select(func.count(Doctor.id))):
            return False
        db.add_all(Doctor(name=n, specialization=s, location=loc, rating=r) for n, s, loc, r in DOCTORS)
        db.commit()
        return True


def seed_demo(session_factory: sessionmaker, now: datetime | None = None) -> bool:
    """Give the demo patient one upcoming cardiology appointment so cancel/reschedule can be demoed."""
    now = now or datetime.now()
    with session_factory() as db:
        if db.scalar(select(Appointment.id).where(Appointment.user_phone == DEMO_PHONE,
                                                  Appointment.status == BOOKED)):
            return False
        doctor = db.scalar(select(Doctor).where(Doctor.specialization == "Cardiology").order_by(Doctor.rating.desc()))
        if doctor is None:
            return False
        start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        while start.weekday() == CLOSED_WEEKDAY:
            start += timedelta(days=1)
        if not db.scalar(select(User.id).where(User.phone == DEMO_PHONE)):
            db.add(User(phone=DEMO_PHONE, name=DEMO_NAME))
        db.add(Appointment(user_phone=DEMO_PHONE, patient_name=DEMO_NAME, doctor_id=doctor.id, slot_start=start,
                           slot_end=start + timedelta(minutes=APPOINTMENT_MINUTES), status=BOOKED))
        db.commit()
        return True
