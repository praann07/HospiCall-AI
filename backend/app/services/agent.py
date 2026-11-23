"""The dialogue agent: a deterministic state machine with an optional, sandboxed LLM fallback.

Design rules (see docs/AGENT_DESIGN.md):
  * Rules decide and act. Emergencies, refusals, verification and every booking/cancel/reschedule are
    handled by code the caller cannot talk their way around.
  * The LLM is used only when the rules do not understand the caller, and it can only *propose language*:
    its intent is re-checked by the same rules and its sentences must pass ``safety.guard_reply``.
  * Anything that reveals or changes an existing appointment needs the caller to prove ownership of the
    phone number (last four digits, three tries), and every write is confirmed with an explicit "yes".
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from ..config import Settings
from . import nlu
from .audit import Audit, mask_phone
from .llm import LLMClient
from .safety import CHEST_PAIN_CAUTION, NO_ADVICE, emergency_reply, guard_reply, self_harm_reply
from .scheduler import (
    APPOINTMENT_MINUTES,
    CLINIC_HOURS,
    AppointmentGone,
    BookingLimit,
    DuplicateBooking,
    Scheduler,
    Slot,
    SlotTaken,
    hour_label,
    resolve_days,
    when_label,
)
from .session import CallSession

MAX_VERIFY_ATTEMPTS = 3
MAX_MISSES = 2          # misunderstandings in one state before we start over
SLOT_CHOICES = 3

NEEDS_VERIFICATION = {"my_appointments", "cancel", "reschedule"}
# Intents that may interrupt whatever the caller was in the middle of.
ESCAPES = NEEDS_VERIFICATION | {"inquiry", "doctor_list", "goodbye", "identity", "human"}
ABORTABLE = {"choose_slot", "confirm_slot", "need_specialty", "need_name", "verify"}
_ABORT = re.compile(r"\b(?:forget (?:it|that)|start over|never ?mind|stop|cancel (?:that|this|it))\b")
_SKIP = re.compile(r"(?:skip|pass|no thanks|no|nope|nah|rather not|i'?d rather not|anonymous)[.! ]*")
_CANCEL_IT = re.compile(r"\bcancel (?:it|that|this)\b")
_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
              "sat": "Saturday", "sun": "Sunday"}
_RELATIVE_DAYS = {"today": "today", "tomorrow": "tomorrow", "day_after": "the day after tomorrow"}

UNCLEAR = ("Sorry, I didn't quite get that. I can book, cancel or reschedule an appointment, or answer "
           "questions about the hospital. What would you like to do?")
MENU = ["Book an appointment", "Cancel appointment", "Hospital hours", "Our doctors"]


@dataclass
class Reply:
    text: str
    intent: str
    path: str = "rules"                  # rules | llm | fallback
    options: list[str] = field(default_factory=list)   # tappable suggestions for the UI
    end_call: bool = False
    outcome: str | None = None           # booked | cancelled | rescheduled | emergency | unverified
    slots: dict = field(default_factory=dict)
    confidence: float = 1.0


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def _phrase(filters: dict) -> str:
    parts = []
    if day := filters.get("day"):
        parts.append(_RELATIVE_DAYS.get(day) or f"on {_DAY_NAMES[day]}")
    if filters.get("daypart"):
        parts.append(f"in the {filters['daypart']}")
    if (hour := filters.get("hour")) is not None:
        parts.append(f"at {hour_label(hour)}")
    return " ".join(parts)


class Agent:
    def __init__(self, scheduler: Scheduler, settings: Settings, llm: LLMClient | None = None,
                 audit: Audit | None = None):
        self.sched = scheduler
        self.s = settings
        self.llm = llm
        self.audit = audit

    # ---- public API ----------------------------------------------------------------------------
    def opening(self) -> str:
        # Deliberately generic: greeting by name would tell anyone who knows a phone number that it is a patient.
        return (f"Hello, you've reached {self.s.hospital_name}. I'm an automated AI assistant. Calls are "
                f"transcribed and saved for quality; say 'don't record' to opt out. How can I help you today?")

    def handle(self, session: CallSession, text: str) -> Reply:
        p = nlu.parse(text)
        self._opt_out(session, p)  # honoured even in the middle of an emergency
        if p.emergency:
            return self._emergency(session, p)
        if p.name and not session.name:
            session.name = p.name
        if p.intent == "no_recording":
            ack = "Understood. I've stopped recording and deleted what was saved so far. "
            return self._resume(session, Reply(ack, "no_recording"))
        prefix = ""
        if p.caution:
            prefix = CHEST_PAIN_CAUTION.format(number=self.s.emergency_number)
        elif p.medical_advice:
            prefix = NO_ADVICE
        reply = self._route(session, p)
        reply.text = prefix + reply.text
        reply.slots = {k: v for k in ("specialty", "day", "daypart", "hour", "doctor_name")
                       if (v := getattr(p, k)) is not None} | reply.slots
        session.last_intent = reply.intent
        return reply

    # ---- safety --------------------------------------------------------------------------------
    def _opt_out(self, session: CallSession, p: nlu.Parsed) -> None:
        if p.no_record and session.record:
            session.record = False
            self._audit(session, "recording_opt_out")

    def _emergency(self, session: CallSession, p: nlu.Parsed) -> Reply:
        self._reset(session)
        self._audit(session, "emergency_escalation", self_harm=p.self_harm)
        text = (self_harm_reply(self.s.emergency_number, self.s.crisis_number) if p.self_harm
                else emergency_reply(self.s.emergency_number))
        return Reply(text, "emergency", outcome="emergency", options=[])  # no booking chip: call for help first

    # ---- routing -------------------------------------------------------------------------------
    def _route(self, session: CallSession, p: nlu.Parsed) -> Reply:
        if session.state:
            if session.state in ABORTABLE and _ABORT.search(p.norm):
                self._reset(session)
                return Reply("No problem, I've dropped that. How else can I help?", "abort", options=MENU)
            reply = getattr(self, f"_st_{session.state}")(session, p)
            if reply:
                return reply
            if not (p.intent in ESCAPES or (p.intent == "book" and (p.specialty or p.doctor_name))):
                return self._miss(session)
            self._reset(session)
        return self._dispatch(session, p)

    def _dispatch(self, session: CallSession, p: nlu.Parsed) -> Reply:
        intent = p.intent
        if intent in NEEDS_VERIFICATION:
            return self._gate(session, p) or (
                self._list_appointments(session) if intent == "my_appointments"
                else self._begin_appointment_flow(session, p, intent))
        if intent == "book":
            return self._start_booking(session, p)
        if intent == "greeting":
            return Reply(f"Hello! I'm the automated assistant for {self.s.hospital_name}. I can book, cancel or "
                         "reschedule appointments and answer questions. How can I help?", intent, options=MENU)
        if intent == "thanks":
            return Reply("You're welcome! Is there anything else I can help with?", intent)
        if intent == "goodbye":
            return Reply(f"Thank you for calling {self.s.hospital_name}. Take care!", intent, end_call=True)
        if intent == "identity":
            return Reply(f"I'm an automated AI assistant for {self.s.hospital_name}, not a human. I can handle "
                         "appointments and hospital information.", intent)
        if intent == "human":
            return Reply("I can't transfer calls because I'm an automated assistant. Reception is open "
                         f"{self.s.hospital_hours} on {self.s.hospital_phone}. I can still help with appointments.",
                         intent)
        if intent == "doctor_list":
            return self._doctor_list(p)
        if intent == "inquiry":
            return Reply(self._inquiry(p.inquiry), intent)
        if intent == "out_of_scope":
            return Reply("I can only help with hospital appointments and information. Would you like to book "
                         "an appointment?", intent, options=MENU)
        return self._fallback(session, p)

    def _fallback(self, session: CallSession, p: nlu.Parsed) -> Reply:
        """Rules did not understand: ask the LLM for a *proposal*, then re-check it with the rules."""
        data = self.llm.route(p.text, session.transcript, self.sched.departments()) if self.llm else None
        if data:
            intent = data["intent"]
            if intent in {"emergency", "self_harm"}:  # the LLM may escalate, never de-escalate
                p.emergency, p.self_harm = True, intent == "self_harm"
                reply = self._emergency(session, p)
                reply.path, reply.confidence = "llm", 0.7
                return reply
            if intent in {"book", "cancel", "reschedule", "my_appointments", "doctor_list", "goodbye"}:
                p.intent, p.specialty = intent, p.specialty or data["specialty"]
                reply = self._dispatch(session, p)
                reply.path, reply.confidence = "llm", 0.7
                return reply
            if (safe := guard_reply(data["reply"])) and intent != "unclear":
                return Reply(safe, intent, path="llm", confidence=0.7)
        return Reply(UNCLEAR, "unclear", path="fallback", options=MENU, confidence=0.3)

    def _miss(self, session: CallSession) -> Reply:
        session.ctx["misses"] = session.ctx.get("misses", 0) + 1
        if session.ctx["misses"] >= MAX_MISSES:
            self._reset(session)
            return Reply("Sorry, I'm having trouble understanding. Let's start over. How can I help?",
                         "unclear", path="fallback", options=MENU, confidence=0.3)
        return Reply("Sorry, I didn't catch that. " + session.ctx["prompt"], "unclear", path="fallback",
                     options=session.ctx["options"], confidence=0.3)

    # ---- state helpers -------------------------------------------------------------------------
    def _enter(self, session: CallSession, state: str, prompt: str, options: list[str], intent: str,
               **ctx) -> Reply:
        session.state, session.ctx = state, {"prompt": prompt, "options": options, "misses": 0, **ctx}
        return Reply(prompt, intent, options=options)

    @staticmethod
    def _reset(session: CallSession) -> None:
        session.state, session.ctx = None, {}

    def _resume(self, session: CallSession, reply: Reply) -> Reply:
        reply.text += session.ctx["prompt"] if session.state else "How can I help?"
        reply.options = session.ctx["options"] if session.state else MENU
        return reply

    def _audit(self, session: CallSession, action: str, **detail) -> None:
        if self.audit:
            self.audit.log(session.call_id, action, phone=mask_phone(session.phone), **detail)

    @staticmethod
    def _filters(p: nlu.Parsed, base: dict | None = None) -> dict:
        found = {k: v for k in ("day", "daypart", "hour") if (v := getattr(p, k)) is not None}
        return {**(base or {}), **found}

    def _doctor(self, doctor_id: int):
        return next(d for d in self.sched.doctors() if d.id == doctor_id)

    # ---- information ---------------------------------------------------------------------------
    def _inquiry(self, kind: str | None) -> str:
        s = self.s
        last_end = f"{CLINIC_HOURS[-1] % 12 or 12}:{APPOINTMENT_MINUTES} {'AM' if CLINIC_HOURS[-1] < 12 else 'PM'}"
        answers = {
            "hours": f"{s.hospital_name} is open {s.hospital_hours}. Appointments run from "
                     f"{hour_label(CLINIC_HOURS[0])} to {last_end}, with a lunch break from 12 to 2 PM.",
            "location": f"We're at {s.hospital_address}.",
            "parking": s.hospital_parking,
            "fees": s.hospital_fees,
            "insurance": s.hospital_insurance,
            "phone": f"You can reach reception on {s.hospital_phone}. For emergencies, call {s.emergency_number}.",
            "emergency_dept": f"Our emergency department is open around the clock. In an emergency, call "
                              f"{s.emergency_number} or come straight to {s.hospital_address}. I'm an automated "
                              "assistant and can't dispatch help myself.",
        }
        return answers.get(kind) or f"{answers['hours']} {answers['location']} {answers['phone']}"

    def _doctor_list(self, p: nlu.Parsed) -> Reply:
        if p.specialty:
            names = [d.name for d in self.sched.doctors(p.specialty)]
            if names:
                return Reply(f"In {p.specialty} we have {_join(names)}. Would you like to book one?", "doctor_list",
                             options=[f"Book {n}" for n in names])
        depts = self.sched.departments()
        return Reply(f"Our departments are {_join(depts)}. Which one would you like?", "doctor_list", options=depts)

    # ---- booking -------------------------------------------------------------------------------
    def _start_booking(self, session: CallSession, p: nlu.Parsed, base_filters: dict | None = None) -> Reply:
        flow = {"purpose": "book", "appt_id": None, "specialty": p.specialty, "doctor_id": None}
        filters = self._filters(p, base_filters)
        if p.doctor_name:
            matches = self.sched.find_doctors_by_name(p.doctor_name)
            if len(matches) == 1:
                flow["doctor_id"], flow["specialty"] = matches[0].id, matches[0].specialization
            else:
                flow["specialty"] = None
                return self._ask_specialty(session, filters, "I couldn't find a doctor by that name. ")
        if not flow["specialty"]:
            return self._ask_specialty(session, filters)
        return self._offer_slots(session, flow, filters)

    def _ask_specialty(self, session: CallSession, filters: dict, prefix: str = "") -> Reply:
        depts = self.sched.departments()
        return self._enter(session, "need_specialty",
                           f"{prefix}Which department would you like? We have {_join(depts)}.", depts, "book",
                           filters=filters)

    def _offer_slots(self, session: CallSession, flow: dict, filters: dict, prefix: str = "") -> Reply:
        """Search, relaxing the filters step by step, then either confirm one slot or offer a short list."""
        intent = flow["purpose"]
        attempts = [filters, {k: v for k, v in filters.items() if k != "hour"},
                    {k: v for k, v in filters.items() if k == "day"}, {}]
        slots, used = [], filters
        for used in attempts:
            # Only a verified caller gets their own held times filtered out (and is told why). For anyone else a
            # missing slot would reveal that the number holds an appointment then, so the list must not depend on it.
            slots = [s for s in self.sched.available_slots(flow["specialty"], doctor_id=flow["doctor_id"],
                                                           limit=SLOT_CHOICES + 1,
                                                           exclude_phone=session.phone if session.verified else None,
                                                           **used)
                     if s.start != flow.get("current_start") and s.start not in flow.get("rejected", ())
                     ][:SLOT_CHOICES]
            if slots:
                break
        if not slots:
            self._reset(session)
            return Reply(f"{prefix}I don't have any openings in {flow['specialty']} this week. Please call reception "
                         f"on {self.s.hospital_phone} and they can help.", intent)
        held = self._skipped_held_time(session, flow, used, slots) if session.verified else None
        if held:
            when = when_label(held, self.sched.now())
            prefix = f"You already have an appointment {when}, so I left that time out. {prefix}"
        relaxed = used != filters
        if {"day", "hour"} <= filters.keys() and not relaxed:
            return self._select_slot(session, slots[0], flow, slots[:1], prefix)
        who = self._doctor(flow["doctor_id"]).name if flow["doctor_id"] else flow["specialty"]
        labels = [when_label(s.start, self.sched.now()) for s in slots]
        miss = f"I don't have anything {_phrase(filters)}. " if relaxed else ""
        text = f"{prefix}{miss}{who} has openings {_join(labels)}. Which one works for you?"
        return self._enter(session, "choose_slot", text, labels, intent, flow=flow, slots=slots)

    def _skipped_held_time(self, session: CallSession, flow: dict, used: dict, offered: list[Slot]) -> datetime | None:
        """A time inside the offered range that we left out because the caller already holds an appointment then."""
        held = {a.start for a in self.sched.upcoming(session.phone)} - {flow.get("current_start")}
        if not held:
            return None
        plain = self.sched.available_slots(flow["specialty"], doctor_id=flow["doctor_id"],
                                           limit=SLOT_CHOICES + len(held), **used)
        return next((s.start for s in plain if s.start in held and s.start <= offered[-1].start), None)

    def _select_slot(self, session: CallSession, slot: Slot, flow: dict, offered: list[Slot],
                     prefix: str = "") -> Reply:
        intent = flow["purpose"]
        if intent == "book" and not session.name and not session.name_declined:
            return self._enter(session, "need_name", prefix + "May I have your name for the booking? You can say skip.",
                               ["Skip"], intent, flow=flow, slot=slot, slots=offered)
        doctor, when = self._doctor(slot.doctor_ids[0]), when_label(slot.start, self.sched.now())
        if intent == "book":
            who = f" for {session.name}" if session.name else ""
            text = f"I can book {doctor.name} ({doctor.specialization}) {when}{who}. Shall I confirm?"
        else:
            text = f"I can move your appointment to {when} with {doctor.name}. Shall I confirm?"
        return self._enter(session, "confirm_slot", prefix + text, ["Yes, confirm", "No"], intent,
                           flow=flow, slot=slot, slots=offered)

    def _commit_slot(self, session: CallSession) -> Reply:
        flow, slot = session.ctx["flow"], session.ctx["slot"]
        booking = flow["purpose"] == "book"
        try:
            appt = (self.sched.book(session.phone, session.name, slot) if booking
                    else self.sched.reschedule(flow["appt_id"], session.phone, slot))
        except DuplicateBooking:
            if not session.verified:
                # Indistinguishable from losing a race for the slot, so an unverified caller learns nothing.
                flow.setdefault("rejected", []).append(slot.start)
                return self._offer_slots(session, flow, {}, "Sorry, that time was just taken. ")
            self._reset(session)
            return Reply("You already have an appointment at that time, so I haven't booked a new one. Would you "
                         "like a different time?", flow["purpose"])
        except SlotTaken:
            return self._offer_slots(session, flow, {}, "Sorry, that time was just taken. ")
        except BookingLimit:
            self._reset(session)
            if not session.verified:  # do not disclose how many appointments a number holds
                self._audit(session, "booking_refused")
                return Reply("I'm not able to add another booking on this call. Please contact reception on "
                             f"{self.s.hospital_phone}.", flow["purpose"], options=MENU)
            return Reply("This phone number already has the maximum number of upcoming appointments, so I can't "
                         f"add another. Please cancel one first, or call reception on {self.s.hospital_phone}.",
                         flow["purpose"], outcome="limit", options=MENU)
        except AppointmentGone:
            self._reset(session)
            return Reply("I can't find that appointment any more, so nothing was changed. How else can I help?",
                         flow["purpose"], options=MENU)
        self._reset(session)
        outcome = "booked" if booking else "rescheduled"
        self._audit(session, f"appointment_{outcome}", appointment_id=appt.id, doctor=appt.doctor_name,
                    slot=appt.start.isoformat())
        when = when_label(appt.start, self.sched.now())
        verb = "You're booked" if booking else "I've moved your appointment. You're now booked"
        return Reply(f"Done! {verb} with {appt.doctor_name} ({appt.specialization}) {when}, {appt.location}. "
                     "Is there anything else I can help with?", flow["purpose"], outcome=outcome,
                     slots={"appointment_id": appt.id})

    # ---- appointment lookup / cancel / reschedule (verified callers only) --------------------------
    def _gate(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        """None if the caller is verified, otherwise the reply that starts or refuses verification."""
        if session.verified:
            return None
        digits = "".join(c for c in session.phone if c.isdigit())
        if len(digits) < 4 or session.verify_attempts >= MAX_VERIFY_ATTEMPTS:
            return Reply("For your privacy I can't access appointment details on this call. Please contact "
                         f"reception on {self.s.hospital_phone}. I can still book a new appointment.",
                         p.intent or "unverified", outcome="unverified", options=["Book an appointment"])
        return self._enter(session, "verify", "To protect your privacy, please tell me the last four digits of the "
                           "phone number you're calling from.", [], p.intent, pending=p)

    def _list_appointments(self, session: CallSession) -> Reply:
        appts = self.sched.upcoming(session.phone)
        if not appts:
            return Reply("You don't have any upcoming appointments. Would you like to book one?",
                         "my_appointments", options=["Book an appointment"])
        now = self.sched.now()
        lines = [f"{a.doctor_name} ({a.specialization}) {when_label(a.start, now)}" for a in appts]
        return Reply(f"You have {len(appts)} upcoming appointment{'s' * (len(appts) > 1)}: {_join(lines)}.",
                     "my_appointments", options=["Cancel appointment", "Reschedule appointment"])

    def _begin_appointment_flow(self, session: CallSession, p: nlu.Parsed, purpose: str) -> Reply:
        appts = self.sched.upcoming(session.phone)
        if not appts:
            return Reply("You don't have any upcoming appointments. Would you like to book one?", purpose,
                         options=["Book an appointment"])
        filters = self._filters(p)
        chosen = appts[0] if len(appts) == 1 else self._match_appointment(appts, p)
        if chosen:
            return self._appointment_chosen(session, chosen, purpose, filters)
        now = self.sched.now()
        labels = [f"{a.doctor_name}, {when_label(a.start, now)}" for a in appts]
        verb = "cancel" if purpose == "cancel" else "reschedule"
        numbered = ", ".join(f"{i}: {label}" for i, label in enumerate(labels, 1))
        return self._enter(session, "choose_appt", f"You have {len(appts)} upcoming appointments. {numbered}. "
                           f"Which one would you like to {verb}?", labels, purpose, appts=appts, purpose=purpose,
                           filters=filters)

    def _match_appointment(self, appts: list, p: nlu.Parsed):
        """The single appointment that fits every detail the caller gave (doctor, day, hour), else None."""
        if not (p.doctor_name or p.day or p.hour is not None):
            return None
        today = self.sched.now().date()
        fits = [a for a in appts
                if (not p.doctor_name or p.doctor_name in a.doctor_name.lower())
                and (p.hour is None or a.start.hour == p.hour)
                and (not p.day or a.start.date() in resolve_days(p.day, today))]
        return fits[0] if len(fits) == 1 else None

    def _appointment_chosen(self, session: CallSession, appt, purpose: str, filters: dict) -> Reply:
        when = when_label(appt.start, self.sched.now())
        if purpose == "cancel":
            return self._enter(session, "confirm_cancel", f"I found your appointment with {appt.doctor_name} "
                               f"({appt.specialization}) {when}. Do you want to cancel it?",
                               ["Yes, cancel it", "Keep it"], purpose, appt=appt)
        flow = {"purpose": "reschedule", "appt_id": appt.id, "specialty": appt.specialization, "doctor_id": None,
                "current_start": appt.start}
        intro = f"Let's move your {when} appointment with {appt.doctor_name}. "
        return self._offer_slots(session, flow, filters, intro)

    # ---- per-state handlers: return a Reply, or None if the caller's words did not fit ------------
    def _st_need_specialty(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        if p.specialty or p.doctor_name:
            return self._start_booking(session, p, session.ctx["filters"])
        return None

    def _st_need_name(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        if p.name:
            session.name = p.name
        elif _SKIP.fullmatch(p.norm):
            session.name_declined = True
        elif p.intent is None and (name := nlu.bare_name(p.text)):
            session.name = name
        else:
            return None
        c = session.ctx
        return self._select_slot(session, c["slot"], c["flow"], c["slots"])

    def _st_choose_slot(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        flow, slots = session.ctx["flow"], session.ctx["slots"]
        if p.specialty and flow["purpose"] == "book" and p.specialty != flow["specialty"]:
            return None  # caller changed their mind about the department: restart the booking
        idx = nlu.pick_ordinal(p.text, len(slots))
        if idx is None and (n := nlu.bare_number(p.text)) and 1 <= n <= len(slots):
            idx = n - 1
        if idx is None and p.hour is not None:
            today = self.sched.now().date()
            hits = [i for i, s in enumerate(slots) if s.start.hour == p.hour
                    and (not p.day or s.start.date() in resolve_days(p.day, today))]
            idx = hits[0] if len(hits) == 1 else None
        if idx is None and len(slots) == 1 and nlu.yes_no(p.text) == "yes":
            idx = 0
        if idx is not None:
            return self._select_slot(session, slots[idx], flow, slots)
        if p.day or p.daypart or p.hour is not None:
            return self._offer_slots(session, flow, self._filters(p))
        if nlu.yes_no(p.text) == "no":
            return self._enter(session, "choose_slot", "No problem. Which day or time would suit you better?",
                               session.ctx["options"], flow["purpose"], flow=flow, slots=slots)
        return None

    def _st_confirm_slot(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        flow, slots = session.ctx["flow"], session.ctx["slots"]
        if p.specialty and flow["purpose"] == "book" and p.specialty != flow["specialty"]:
            return None
        answer = nlu.yes_no(p.text)
        if answer == "yes":
            return self._commit_slot(session)
        if p.day or p.daypart or p.hour is not None:
            return self._offer_slots(session, flow, self._filters(p))
        if answer == "no":
            return self._enter(session, "choose_slot", "No problem. Which day or time would suit you better?",
                               [when_label(s.start, self.sched.now()) for s in slots], flow["purpose"],
                               flow=flow, slots=slots)
        return None

    def _st_verify(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        entered = nlu.parse_digits(p.text)
        if len(entered) < 4:
            return None
        pending = session.ctx["pending"]
        session.verify_attempts += 1
        if "".join(c for c in session.phone if c.isdigit()).endswith(entered):
            session.verified = True
            session.name = session.name or self.sched.patient_name(session.phone)
            self._audit(session, "verify_success")
            self._reset(session)
            reply = self._dispatch(session, pending)
            reply.text = "Thank you, you're verified. " + reply.text
            return reply
        self._audit(session, "verify_failed", attempts=session.verify_attempts)
        if session.verify_attempts >= MAX_VERIFY_ATTEMPTS:
            self._reset(session)
            return self._gate(session, pending)
        return self._enter(session, "verify", "Sorry, that doesn't match our records. Please say the last four "
                           "digits of your phone number again.", [], pending.intent, pending=pending)

    def _st_choose_appt(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        appts, purpose = session.ctx["appts"], session.ctx["purpose"]
        idx = nlu.pick_ordinal(p.text, len(appts))
        if idx is None and (n := nlu.bare_number(p.text)) and 1 <= n <= len(appts):
            idx = n - 1
        chosen = appts[idx] if idx is not None else self._match_appointment(appts, p)
        if not chosen:
            return None
        return self._appointment_chosen(session, chosen, purpose, session.ctx["filters"])

    def _st_confirm_cancel(self, session: CallSession, p: nlu.Parsed) -> Reply | None:
        answer = nlu.yes_no(p.text) or ("yes" if _CANCEL_IT.search(p.norm) else None)
        if answer is None:
            return None
        appt = session.ctx["appt"]
        self._reset(session)
        if answer == "no":
            return Reply("Okay, I've kept your appointment. Is there anything else I can help with?", "cancel")
        cancelled = self.sched.cancel(appt.id, session.phone)
        if not cancelled:
            return Reply("I couldn't cancel that appointment; it may already be cancelled or in the past.", "cancel")
        self._audit(session, "appointment_cancelled", appointment_id=appt.id)
        return Reply(f"Done. I've cancelled your appointment with {cancelled.doctor_name} "
                     f"{when_label(cancelled.start, self.sched.now())}. Would you like to book another?", "cancel",
                     outcome="cancelled", slots={"appointment_id": appt.id}, options=["Book an appointment"])
