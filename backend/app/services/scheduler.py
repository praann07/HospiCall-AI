"""Appointment availability and booking.

The database is the source of truth for double-booking: ``Appointment`` has partial unique indexes on
(doctor_id, slot_start) and (user_phone, slot_start) for booked rows, so ``book`` simply tries each free
doctor and treats an ``IntegrityError`` as "someone beat us to it". No in-process lock is needed, so it
stays correct with several workers.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ..db import BOOKED, CANCELLED, Appointment, Doctor, User

CLINIC_HOURS = (9, 10, 11, 14, 15, 16, 17)   # hourly slot starts; lunch break 12-14
APPOINTMENT_MINUTES = 30
CLOSED_WEEKDAY = 6                           # Sunday
SEARCH_DAYS = 7
BOOKING_LEAD = timedelta(minutes=30)         # cannot book a slot that starts sooner than this
MAX_UPCOMING = 3                             # one phone number cannot hoard more upcoming appointments than this
DAYPARTS = {"morning": range(0, 12), "afternoon": range(12, 16), "evening": range(16, 24)}
_WEEKDAY_TOKENS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class SlotTaken(Exception):
    """The slot is gone (booked by someone else, in the past, or not a real clinic slot)."""


class DuplicateBooking(Exception):
    """The patient already has an appointment at that time."""


class AppointmentGone(Exception):
    """The appointment to cancel/move no longer exists, is not theirs, or is not active."""


class BookingLimit(Exception):
    """The phone number already holds MAX_UPCOMING upcoming appointments."""


@dataclass(frozen=True)
class DoctorView:
    id: int
    name: str
    specialization: str
    location: str
    rating: float


@dataclass(frozen=True)
class Slot:
    start: datetime
    doctor_ids: tuple[int, ...]   # every doctor free at this time, best-rated first


@dataclass(frozen=True)
class ApptView:
    id: int
    doctor_id: int
    doctor_name: str
    specialization: str
    location: str
    start: datetime
    end: datetime
    patient_name: str | None = None


def hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def when_label(moment: datetime, now: datetime) -> str:
    """'today at 4 PM', 'tomorrow at 10 AM', 'Friday at 9 AM', 'Monday 30 November at 2 PM'."""
    days = (moment.date() - now.date()).days
    if days == 0:
        day = "today"
    elif days == 1:
        day = "tomorrow"
    elif 1 < days < 7:
        day = _WEEKDAY_NAMES[moment.weekday()]
    else:
        day = f"{_WEEKDAY_NAMES[moment.weekday()]} {moment.day} {moment.strftime('%B')}"
    minute = f":{moment.minute:02d}" if moment.minute else ""
    return f"{day} at {moment.hour % 12 or 12}{minute} {'AM' if moment.hour < 12 else 'PM'}"


def resolve_days(token: str | None, today: date) -> list[date]:
    """Day token from the NLU -> concrete dates to search. No token means the next SEARCH_DAYS days."""
    if token is None:
        return [today + timedelta(days=i) for i in range(SEARCH_DAYS)]
    if token == "today":
        return [today]
    if token == "tomorrow":
        return [today + timedelta(days=1)]
    if token == "day_after":
        return [today + timedelta(days=2)]
    target = _WEEKDAY_TOKENS.index(token)
    first = today + timedelta(days=(target - today.weekday()) % 7)
    return [first, first + timedelta(days=7)]  # "Monday" said on Monday: today if slots remain, else next week


def _doctor_view(d: Doctor) -> DoctorView:
    return DoctorView(d.id, d.name, d.specialization, d.location, d.rating)


class Scheduler:
    def __init__(self, session_factory: sessionmaker, now_fn: Callable[[], datetime] = datetime.now):
        self._Session = session_factory
        self.now = now_fn

    # ---- reads -------------------------------------------------------------------------------
    def departments(self) -> list[str]:
        with self._Session() as db:
            return sorted(set(db.scalars(select(Doctor.specialization))))

    def doctors(self, specialty: str | None = None) -> list[DoctorView]:
        stmt = select(Doctor).order_by(Doctor.rating.desc(), Doctor.id)
        if specialty:
            stmt = stmt.where(Doctor.specialization == specialty)
        with self._Session() as db:
            return [_doctor_view(d) for d in db.scalars(stmt)]

    def find_doctors_by_name(self, fragment: str) -> list[DoctorView]:
        fragment = fragment.strip().lower()
        if len(fragment) < 2:
            return []
        return [d for d in self.doctors() if fragment in d.name.lower()]

    def available_slots(self, specialty: str | None = None, *, doctor_id: int | None = None,
                        day: str | None = None, daypart: str | None = None, hour: int | None = None,
                        limit: int = 3, exclude_phone: str | None = None) -> list[Slot]:
        """Next free slots matching the filters, soonest first.

        With ``exclude_phone`` the times at which that patient already holds an appointment are skipped, so a
        caller is never offered a slot that ``book`` would then refuse as a duplicate.
        """
        now = self.now()
        doctors = [d for d in self.doctors(specialty) if doctor_id in (None, d.id)]
        if not doctors:
            return []
        dates = resolve_days(day, now.date())
        window_start = datetime.combine(dates[0], datetime.min.time())
        window_end = datetime.combine(dates[-1] + timedelta(days=1), datetime.min.time())
        with self._Session() as db:
            taken = {(row.doctor_id, row.slot_start) for row in db.execute(
                select(Appointment.doctor_id, Appointment.slot_start).where(
                    Appointment.status == BOOKED, Appointment.slot_start >= window_start,
                    Appointment.slot_start < window_end, Appointment.doctor_id.in_([d.id for d in doctors])))}
            mine = set(db.scalars(
                select(Appointment.slot_start).where(
                    Appointment.status == BOOKED, Appointment.user_phone == exclude_phone,
                    Appointment.slot_start >= window_start, Appointment.slot_start < window_end))
            ) if exclude_phone else set()
        slots: list[Slot] = []
        for current in dates:
            if current.weekday() == CLOSED_WEEKDAY:
                continue
            for h in CLINIC_HOURS:
                start = datetime.combine(current, datetime.min.time()).replace(hour=h)
                if start < now + BOOKING_LEAD or start in mine:
                    continue
                if hour is not None and h != hour:
                    continue
                if daypart is not None and h not in DAYPARTS[daypart]:
                    continue
                free = tuple(d.id for d in doctors if (d.id, start) not in taken)
                if free:
                    slots.append(Slot(start, free))
                    if len(slots) >= limit:
                        return slots
        return slots

    def patient_name(self, phone: str) -> str | None:
        with self._Session() as db:
            return db.scalar(select(User.name).where(User.phone == phone))

    def upcoming(self, phone: str) -> list[ApptView]:
        stmt = (select(Appointment, Doctor).join(Doctor, Doctor.id == Appointment.doctor_id)
                .where(Appointment.user_phone == phone, Appointment.status == BOOKED,
                       Appointment.slot_start >= self.now())
                .order_by(Appointment.slot_start))
        with self._Session() as db:
            return [self._appt_view(a, d) for a, d in db.execute(stmt)]

    # ---- writes ------------------------------------------------------------------------------
    def book(self, phone: str, name: str | None, slot: Slot) -> ApptView:
        """Book the best free doctor for ``slot``. Raises SlotTaken / DuplicateBooking / BookingLimit."""
        self._check_bookable(slot.start)
        with self._Session() as db:
            held = db.scalar(select(func.count(Appointment.id)).where(
                Appointment.user_phone == phone, Appointment.status == BOOKED,
                Appointment.slot_start >= self.now()))
            if held >= MAX_UPCOMING:
                raise BookingLimit(f"{phone[-4:]} already holds {held} upcoming appointments")
            self._ensure_user(db, phone, name)
            appt = self._insert_first_free(db, phone, name, slot)
            db.commit()
            return self._view(db, appt)

    def cancel(self, appointment_id: int, phone: str) -> ApptView | None:
        """Cancel one of the caller's own upcoming appointments. None if it is not theirs or not active."""
        with self._Session() as db:
            appt = db.get(Appointment, appointment_id)
            if not appt or appt.user_phone != phone or appt.status != BOOKED or appt.slot_start < self.now():
                return None
            appt.status = CANCELLED
            db.commit()
            return self._view(db, appt)

    def reschedule(self, appointment_id: int, phone: str, slot: Slot) -> ApptView:
        """Atomically move an appointment: book the new slot, and only then release the old one."""
        self._check_bookable(slot.start)
        with self._Session() as db:
            old = db.get(Appointment, appointment_id)
            if not old or old.user_phone != phone or old.status != BOOKED:
                raise AppointmentGone("original appointment not found")
            new = self._insert_first_free(db, phone, old.patient_name, slot)
            old.status = CANCELLED
            db.commit()
            return self._view(db, new)

    # ---- internals ---------------------------------------------------------------------------
    def _check_bookable(self, start: datetime) -> None:
        legal = (start.minute == 0 and start.second == 0 and start.hour in CLINIC_HOURS
                 and start.weekday() != CLOSED_WEEKDAY)
        if not legal or start < self.now() + BOOKING_LEAD:
            raise SlotTaken("not a bookable clinic slot")

    @staticmethod
    def _ensure_user(db, phone: str, name: str | None) -> None:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is None:
            try:
                with db.begin_nested():  # two first calls from one number race on users.phone
                    db.add(User(phone=phone, name=name))
                    db.flush()
                return
            except IntegrityError:
                user = db.scalar(select(User).where(User.phone == phone))
        if name and not user.name:
            user.name = name

    @staticmethod
    def _holds(db, phone: str, start: datetime) -> bool:
        return db.scalar(select(Appointment.id).where(
            Appointment.user_phone == phone, Appointment.status == BOOKED,
            Appointment.slot_start == start)) is not None

    @classmethod
    def _insert_first_free(cls, db, phone: str, name: str | None, slot: Slot) -> Appointment:
        if cls._holds(db, phone, slot.start):
            raise DuplicateBooking("patient already booked at that time")
        end = slot.start + timedelta(minutes=APPOINTMENT_MINUTES)
        for doctor_id in slot.doctor_ids:
            appt = Appointment(user_phone=phone, patient_name=name, doctor_id=doctor_id,
                               slot_start=slot.start, slot_end=end, status=BOOKED)
            try:
                with db.begin_nested():  # savepoint: a lost race only rolls back this insert
                    db.add(appt)
                    db.flush()
                return appt
            except IntegrityError:
                if cls._holds(db, phone, slot.start):  # lost to the patient index, not to another patient
                    raise DuplicateBooking("patient already booked at that time") from None
                continue
        raise SlotTaken("every doctor was booked for that slot")

    @staticmethod
    def _appt_view(a: Appointment, d: Doctor) -> ApptView:
        return ApptView(a.id, d.id, d.name, d.specialization, d.location, a.slot_start, a.slot_end, a.patient_name)

    def _view(self, db, appt: Appointment) -> ApptView:
        return self._appt_view(appt, db.get(Doctor, appt.doctor_id))
