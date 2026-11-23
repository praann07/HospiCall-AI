"""Seed the database with demo doctors and a demo patient appointment (idempotent).

Run from the backend/ directory:  python -m scripts.seed_db
"""
from app.config import settings
from app.db import init_db
from app.seed import DEMO_PHONE, seed_demo, seed_if_empty


def main() -> None:
    session_factory = init_db(settings.db_path)
    print(f"Database: {settings.db_path}")
    print("Doctors seeded." if seed_if_empty(session_factory) else "Doctors already present.")
    print("Demo appointment seeded." if seed_demo(session_factory) else "Demo appointment already present.")
    print(f"Demo patient phone: {DEMO_PHONE}")


if __name__ == "__main__":
    main()
