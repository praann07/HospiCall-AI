"""Scheduler: availability maths, double-booking protection, cancel/reschedule ownership."""
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import BOOKED, Appointment, init_db
from app.seed import seed_if_empty
from app.services.scheduler import (
    MAX_UPCOMING,
    AppointmentGone,
    BookingLimit,
    DuplicateBooking,
    Scheduler,
    SlotTaken,
    hour_label,
    resolve_days,
    when_label,
)

from .conftest import PHONE

NOW = datetime(2026, 11, 23, 8, 0)  # Monday
TOMORROW_10 = datetime(2026, 11, 24, 10, 0)


def slot_at(scheduler, specialty, start):
    """The offered Slot that starts at `start` (fails the test if it is not offered)."""
    for slot in scheduler.available_slots(specialty, limit=200):
        if slot.start == start:
            return slot
    raise AssertionError(f"{specialty} has no free slot at {start}")


# ---------------------------------------------------------------------------------------------- directory
def test_departments_and_doctors_are_seeded(scheduler):
    assert "Cardiology" in scheduler.departments()
    assert {d.name for d in scheduler.doctors("Cardiology")} == {"Dr. Mehta", "Dr. Rao"}
    assert [d.name for d in scheduler.doctors("Neurology")] == ["Dr. Iyer"]


def test_best_rated_doctor_comes_first(scheduler):
    assert scheduler.doctors("Cardiology")[0].name == "Dr. Mehta"


def test_find_doctor_by_name_fragment(scheduler):
    assert [d.name for d in scheduler.find_doctors_by_name("iyer")] == ["Dr. Iyer"]
    assert scheduler.find_doctors_by_name("x") == []


# ---------------------------------------------------------------------------------------------- availability
def test_slots_respect_booking_lead_time(scheduler, clock):
    first = scheduler.available_slots("Cardiology", limit=1)[0]
    assert first.start == datetime(2026, 11, 23, 9, 0)
    clock.now = datetime(2026, 11, 23, 8, 45)  # < 30 min before 9 AM
    assert scheduler.available_slots("Cardiology", limit=1)[0].start == datetime(2026, 11, 23, 10, 0)


def test_sunday_is_closed(scheduler):
    sunday = [s for s in scheduler.available_slots("Cardiology", limit=200) if s.start.weekday() == 6]
    assert sunday == []


def test_day_and_daypart_filters(scheduler):
    morning = scheduler.available_slots("Cardiology", day="tomorrow", daypart="morning", limit=10)
    assert morning and all(s.start.date() == TOMORROW_10.date() and s.start.hour < 12 for s in morning)
    evening = scheduler.available_slots("Cardiology", day="tomorrow", daypart="evening", limit=10)
    assert evening and all(s.start.hour >= 16 for s in evening)


def test_exact_hour_filter(scheduler):
    slots = scheduler.available_slots("Cardiology", day="tomorrow", hour=10, limit=10)
    assert [s.start for s in slots] == [TOMORROW_10]


def test_booked_slot_disappears_for_single_doctor_department(scheduler):
    slot = slot_at(scheduler, "Neurology", TOMORROW_10)
    scheduler.book(PHONE, "Asha", slot)
    assert TOMORROW_10 not in [s.start for s in scheduler.available_slots("Neurology", limit=200)]


def test_second_booking_in_two_doctor_department_goes_to_other_doctor(scheduler):
    first = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Cardiology", TOMORROW_10))
    second = scheduler.book("9000000001", "Ben", slot_at(scheduler, "Cardiology", TOMORROW_10))
    assert {first.doctor_name, second.doctor_name} == {"Dr. Mehta", "Dr. Rao"}
    assert TOMORROW_10 not in [s.start for s in scheduler.available_slots("Cardiology", limit=200)]


def test_exclude_phone_hides_times_the_patient_already_holds(scheduler):
    scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    others = [s.start for s in scheduler.available_slots("Cardiology", day="tomorrow", limit=20, exclude_phone=PHONE)]
    everyone = [s.start for s in scheduler.available_slots("Cardiology", day="tomorrow", limit=20)]
    assert TOMORROW_10 in everyone and TOMORROW_10 not in others


# ---------------------------------------------------------------------------------------------- booking rules
def test_stale_slot_raises_slot_taken(scheduler):
    stale = slot_at(scheduler, "Neurology", TOMORROW_10)
    scheduler.book("9000000001", "Ben", stale)
    with pytest.raises(SlotTaken):
        scheduler.book(PHONE, "Asha", stale)


def test_same_patient_cannot_double_book_same_time_across_departments(scheduler):
    scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    with pytest.raises(DuplicateBooking):
        scheduler.book(PHONE, "Asha", slot_at(scheduler, "Orthopedics", TOMORROW_10))


@pytest.mark.parametrize("start", [
    datetime(2026, 11, 24, 8, 0),    # before opening
    datetime(2026, 11, 24, 13, 0),   # lunch break
    datetime(2026, 11, 29, 10, 0),   # Sunday
    datetime(2026, 11, 22, 10, 0),   # past
    datetime(2026, 11, 24, 10, 15),  # not on the hour
])
def test_illegal_slots_are_rejected(scheduler, start):
    legit = slot_at(scheduler, "Neurology", TOMORROW_10)
    forged = type(legit)(start=start, doctor_ids=legit.doctor_ids)
    with pytest.raises(SlotTaken):
        scheduler.book(PHONE, "Asha", forged)


def test_appointment_is_30_minutes_and_listed_for_owner_only(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    assert appt.end - appt.start == timedelta(minutes=30)
    assert [a.id for a in scheduler.upcoming(PHONE)] == [appt.id]
    assert scheduler.upcoming("9000000001") == []
    assert scheduler.patient_name(PHONE) == "Asha"


# ---------------------------------------------------------------------------------------------- cancel
def test_cancel_frees_slot_and_is_owner_only(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    assert scheduler.cancel(appt.id, "9000000001") is None  # not the owner
    assert scheduler.cancel(appt.id, PHONE).id == appt.id
    assert scheduler.cancel(appt.id, PHONE) is None  # idempotent
    assert TOMORROW_10 in [s.start for s in scheduler.available_slots("Neurology", limit=200)]
    assert scheduler.upcoming(PHONE) == []


def test_cancelled_slot_can_be_rebooked(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    scheduler.cancel(appt.id, PHONE)
    again = scheduler.book("9000000001", "Ben", slot_at(scheduler, "Neurology", TOMORROW_10))
    assert again.doctor_name == "Dr. Iyer"


# ---------------------------------------------------------------------------------------------- reschedule
def test_reschedule_moves_appointment_and_releases_old_slot(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    new_start = datetime(2026, 11, 25, 11, 0)
    moved = scheduler.reschedule(appt.id, PHONE, slot_at(scheduler, "Neurology", new_start))
    assert moved.start == new_start
    assert [a.start for a in scheduler.upcoming(PHONE)] == [new_start]
    assert TOMORROW_10 in [s.start for s in scheduler.available_slots("Neurology", limit=200)]


def test_failed_reschedule_keeps_original(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    target = slot_at(scheduler, "Neurology", datetime(2026, 11, 25, 11, 0))
    scheduler.book("9000000001", "Ben", target)  # someone else takes it first
    with pytest.raises(SlotTaken):
        scheduler.reschedule(appt.id, PHONE, target)
    assert [a.start for a in scheduler.upcoming(PHONE)] == [TOMORROW_10]


def test_cannot_reschedule_someone_elses_appointment(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    target = slot_at(scheduler, "Neurology", datetime(2026, 11, 25, 11, 0))
    with pytest.raises(AppointmentGone):
        scheduler.reschedule(appt.id, "9000000001", target)
    assert [a.start for a in scheduler.upcoming(PHONE)] == [TOMORROW_10]


def test_reschedule_of_a_cancelled_appointment_reports_it_gone(scheduler):
    appt = scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", TOMORROW_10))
    scheduler.cancel(appt.id, PHONE)
    with pytest.raises(AppointmentGone):
        scheduler.reschedule(appt.id, PHONE, slot_at(scheduler, "Neurology", datetime(2026, 11, 25, 11, 0)))


def test_one_phone_cannot_hoard_appointments(scheduler):
    for day in range(MAX_UPCOMING):
        scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", datetime(2026, 11, 24 + day, 10, 0)))
    with pytest.raises(BookingLimit):
        scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", datetime(2026, 11, 24 + MAX_UPCOMING, 10, 0)))
    assert len(scheduler.upcoming(PHONE)) == MAX_UPCOMING


def test_cancelling_frees_a_place_under_the_limit(scheduler):
    booked = [scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", datetime(2026, 11, 24 + d, 10, 0)))
              for d in range(MAX_UPCOMING)]
    scheduler.cancel(booked[0].id, PHONE)
    scheduler.book(PHONE, "Asha", slot_at(scheduler, "Neurology", datetime(2026, 11, 24 + MAX_UPCOMING, 10, 0)))


def test_slot_inside_the_booking_lead_time_is_rejected(scheduler):
    legit = slot_at(scheduler, "Neurology", TOMORROW_10)
    too_soon = type(legit)(start=NOW + timedelta(minutes=10), doctor_ids=legit.doctor_ids)
    with pytest.raises(SlotTaken):
        scheduler.book(PHONE, "Asha", too_soon)


def test_database_rejects_two_booked_rows_for_one_patient_and_time(session_factory):
    with session_factory() as db:
        for doctor_id in (1, 2):
            db.add(Appointment(user_phone=PHONE, doctor_id=doctor_id, slot_start=TOMORROW_10,
                               slot_end=TOMORROW_10 + timedelta(minutes=30), status=BOOKED))
        with pytest.raises(IntegrityError):
            db.commit()


# ---------------------------------------------------------------------------------------------- concurrency
def test_concurrent_bookings_of_one_slot_yield_exactly_one_winner(tmp_path):
    factory = init_db(str(tmp_path / "race.db"))
    seed_if_empty(factory)
    sched = Scheduler(factory, lambda: NOW)
    slot = slot_at(sched, "Neurology", TOMORROW_10)
    workers = 8
    barrier = threading.Barrier(workers)
    outcomes: list[str] = []

    def attempt(i: int) -> None:
        barrier.wait()
        try:
            sched.book(f"900000000{i}", f"Patient {i}", slot)
            outcomes.append("ok")
        except SlotTaken:
            outcomes.append("taken")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["ok"] + ["taken"] * (workers - 1)
    with factory() as db:
        assert db.query(Appointment).filter_by(status=BOOKED).count() == 1


def test_database_itself_rejects_a_double_booking(session_factory):
    """The partial unique index is the last line of defence even if application checks are bypassed."""
    with session_factory() as db:
        for phone in ("9000000001", "9000000002"):
            db.add(Appointment(user_phone=phone, doctor_id=1, slot_start=TOMORROW_10,
                               slot_end=TOMORROW_10 + timedelta(minutes=30), status=BOOKED))
        with pytest.raises(IntegrityError):
            db.commit()


# ---------------------------------------------------------------------------------------------- labels
@pytest.mark.parametrize("hour, label", [(0, "12 AM"), (9, "9 AM"), (12, "12 PM"), (15, "3 PM")])
def test_hour_label(hour, label):
    assert hour_label(hour) == label


@pytest.mark.parametrize("moment, label", [
    (datetime(2026, 11, 23, 16, 0), "today at 4 PM"),
    (datetime(2026, 11, 24, 10, 0), "tomorrow at 10 AM"),
    (datetime(2026, 11, 27, 9, 0), "Friday at 9 AM"),
    (datetime(2026, 11, 30, 14, 0), "Monday 30 November at 2 PM"),
    (datetime(2026, 11, 24, 10, 30), "tomorrow at 10:30 AM"),
])
def test_when_label(moment, label):
    assert when_label(moment, NOW) == label


def test_resolve_days():
    today = NOW.date()
    assert len(resolve_days(None, today)) == 7
    assert resolve_days("today", today) == [today]
    assert resolve_days("tomorrow", today) == [today + timedelta(days=1)]
