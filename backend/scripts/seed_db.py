"""Seed the database with sample doctors."""
import sys
import os
from datetime import datetime, timedelta

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.config import settings
from app.db import init_db, Doctor, Appointment

Session = init_db(settings.db_path)

doctors = [
    {"name": "Dr. Mehta", "specialization": "Cardiology", "location": "Block A, 2F", "rating": 4.8},
    {"name": "Dr. Rao", "specialization": "Cardiology", "location": "Block A, 2F", "rating": 4.6},
    {"name": "Dr. Iyer", "specialization": "Neurology", "location": "Block B, 1F", "rating": 4.9},
    {"name": "Dr. Kapoor", "specialization": "Orthopedics", "location": "Block C, 3F", "rating": 4.5},
    {"name": "Dr. Nair", "specialization": "Pediatrics", "location": "Block B, 2F", "rating": 4.7},
]

with Session() as db:
    if db.query(Doctor).count() == 0:
        for d in doctors:
            db.add(Doctor(**d))
        db.commit()
        print(f"Seeded {len(doctors)} doctors")
    else:
        print("Doctors already seeded")

    # a demo patient
    from app.db import User
    if not db.query(User).filter_by(phone="9441321662").first():
        db.add(User(phone="9441321662", name="Demo Patient"))
        db.commit()
        print("Seeded demo patient 9441321662")

    # demo appointment for the transcript view
    if db.query(Appointment).count() == 0:
        doc = db.query(Doctor).filter_by(specialization="Cardiology").first()
        if doc:
            slot = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
            db.add(Appointment(user_phone="9441321662", doctor_id=doc.id,
                               slot_start=slot, slot_end=slot + timedelta(minutes=30)))
            db.commit()
            print("Seeded demo appointment")
