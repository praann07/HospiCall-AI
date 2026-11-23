# Agent design

How HospiCall decides what to say and do, why it is built that way, and how that is verified.
This describes the code as it is today (`backend/app/services/`); the older pre-implementation documents live in
[`design/`](design/) and are kept for history only.

## 1. The core decision: rules act, the LLM suggests

A hospital receptionist has two jobs that pull in opposite directions:

| Job | Needs | Best served by |
|---|---|---|
| Understand messy human language | flexibility | a language model |
| Change records (book, cancel), refuse medical advice, handle emergencies | predictability, auditability | deterministic code |

So the roles are split:

* **`nlu.py` + `agent.py` (rules)** parse the utterance, own the dialogue state, and are the *only* code that can touch the
  database (through `scheduler.py`).
* **`llm.py` (optional)** is consulted *only when the rules return no intent*. It returns one JSON object
  `{intent, specialty, reply}` constrained by a JSON schema (Ollama `format`), and the answer is validated against an
  allow-list. The LLM has no tools, no database access and no way to skip a gate.

The pattern is "LLM as router/paraphraser, code as executor". The agent maps the proposed intent back onto its own
intents, so a proposed `cancel` still hits the verification gate and the confirmation step exactly as if the caller had
said "cancel". Free text from the model is only spoken if it passes `safety.guard_reply`, which rejects dosages,
diagnoses, prescriptions, drug names, URLs, six-plus digit runs, prompt-injection echoes and over-long replies.
If the model is down, slow or off-schema, the call degrades to a short menu instead of hanging.

Why not an LLM-first agent with tool calling? For this domain a mis-booked or mis-cancelled appointment, or a "friendly"
dosage suggestion, costs more than a clumsy sentence. The fast path also answers in ~1 ms on a laptop CPU, with no GPU,
which is what makes the project realistic to self-host.

## 2. Turn pipeline

```
text ──► parse (nlu) ──► safety checks ──► active state handler? ──► intent dispatch ──► Reply
                             │                    │                        │
                  emergency / self-harm     choose_slot, confirm,     rules, else LLM proposal
                  interrupt ANY state       verify, choose_appt …     (re-validated), else menu
```

1. **Safety first, in every state.** Emergency and self-harm phrases are checked before anything else, including in the
   middle of a booking. The flow state is reset and the reply is the safety reply.
2. **Caller privacy controls** ("don't record") are honoured immediately.
3. **Active state handler.** If the conversation is mid-flow, the handler for that state interprets the utterance
   (a slot choice, a yes/no, a verification code…). Strong "escape" intents (cancel, hours, goodbye…) can break out of a
   flow; two consecutive misses reset it with a "let's start over" message rather than looping forever.
4. **Intent dispatch.** Otherwise the parsed intent is routed to its handler.
5. **LLM fallback** only if no rule matched (intent `None`).

The dialogue state machine (`session.state` + `session.ctx`):

| State | Waiting for |
|---|---|
| `need_specialty` | which department / doctor |
| `choose_slot` | one of the offered slots (ordinals, "earliest", an hour, "the second one") |
| `need_name` | a name, or "skip" |
| `confirm_slot` | explicit yes/no for a booking or a reschedule |
| `verify` | last four digits of the caller's number |
| `choose_appt` | which appointment, when the patient has several |
| `confirm_cancel` | explicit yes/no for a cancellation |

Design rules that fell out of testing:

* **Every state-changing action ends with an explicit confirmation turn** (book, cancel, reschedule) — speech
  recognition errors are common, so "did you mean it?" is the cheapest safeguard.
* **Slot filters are relaxed, not abandoned.** If "Friday morning with Dr. Iyer" has nothing free, the agent says what it
  could not find and offers the next openings instead of dead-ending.
* **A name is only captured from explicit phrasing** ("my name is …", "this is …", "call me …") or when it is the only
  thing said in response to the name question. An earlier keyword approach turned "I am looking for a doctor" into the
  patient name "Looking"; there is a regression test for it.

## 3. NLU

`nlu.parse(text) -> Parsed` is a pure function: normalise → word-boundary regexes → a dataclass of intent, inquiry kind,
specialty, doctor name, day, daypart, hour, name and safety flags.

* Word-boundary matching everywhere (`\bear\b`, not `"ear" in text`), after the original substring matcher fired on
  "year", "clear" and "which".
* Strong booking words (`book`, `schedule`, `appointment`, `slot`, `reserve`) vs weak ones (`consultation`, `visit`,
  `available`). Questions like "how much is the consultation fee" must be answered, not turned into a booking.
* "Chest pain" is *cardiology plus a caution* ("if it is severe or happening now, call 108 first"), not an automatic
  emergency — otherwise every cardiology booking would be hijacked. Strong phrases ("severe chest pain", "not breathing",
  "unconscious", "heavy bleeding", "this is an emergency") always escalate; a bare "emergency" escalates unless it is
  followed by "department", "ward", "hours", "number" and so on.
* Regex rather than a trained classifier because it is explainable, ~free, has no training-data requirement and gets
  100 % on the labelled set. The honest limitation is paraphrase coverage — that is exactly what the LLM fallback is for.

## 4. Guardrails

| Risk | Control | Where |
|---|---|---|
| Caller in danger | emergency / self-harm reply works in every state; **honest** — says it is an automated assistant that cannot send help, gives 108 and a crisis line (14416) | `safety.py`, `agent.handle` |
| Medical advice | refusal text + offer to book; LLM output blocked by `guard_reply` | `safety.py` |
| Prompt injection | the LLM has no tools and no database access; it sees only the last 6 redacted turns (digits masked, a spoken name may appear); output is schema-checked and filtered; injection phrases in replies are rejected; it may escalate to emergency but never de-escalate | `llm.py`, `safety.py`, `agent._fallback` |
| PHI disclosure | listing / cancelling / rescheduling requires last-4 verification, 3 attempts, then the call is closed out as `unverified`; attempted codes and any 7+ digit run are stored masked; the opening greeting is generic (no name before verification); an unverified caller's slot lists and refusals never depend on what their number holds (a held time looks like a lost race; the booking-limit refusal is generic), so there is no appointment oracle; held times are filtered and explained only once verified | `agent._gate`, `calls.py` |
| Cross-patient access | scheduler `cancel` / `upcoming` are scoped by the caller's phone number | `scheduler.py` |
| Double booking | partial unique index `(doctor_id, slot_start) WHERE status='booked'` + savepoint + `IntegrityError` handling; one patient cannot hold two appointments at the same time | `db.py`, `scheduler.py` |
| Recording consent | greeting discloses "automated AI assistant", "calls are transcribed"; "don't record" stops saving **and erases** the saved turns | `agent.opening()`, `calls.py` |
| Path traversal / abuse | server-generated `call_id`, regex-validated on input; body caps rejected before parsing (413 / 411 / 400); MIME check; temp audio deleted after use; global 120/min plus stricter per-route limits (start 10/min, audio + synthesize 30/min), IPv6 keyed by /64, `X-Forwarded-For` deliberately not trusted; bounded rate-limiter and session memory | `api.py`, `bodylimit.py`, `ratelimit.py`, `session.py` |
| Browser hardening | CSP (`default-src 'self'`, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `Permissions-Policy`, `nosniff`, `no-store`; validation errors never echo the input | `api.py` |
| Admin data | `X-API-Key` (constant-time compare), off unless configured, phone numbers masked; the stored-transcript endpoint is admin-only | `api.py`, `audit.py` |

Not guarded, deliberately: caller identity is "last four digits of the number you called from". Real telephony supplies
a trustworthy caller ID; the browser demo does not. That limit is documented rather than hidden.

Consequently an unverified caller can still *infer* a little by attempting bookings under someone else's number: a
listed slot answered with "just taken", or a generic refusal after three bookings, hints that the number already holds
an appointment. Anyone able to claim the number can also pass last-4 verification, so closing this fully needs real
caller authentication (carrier caller ID or a one-time code), not more wording tricks. Until then nothing about the
appointments themselves is ever disclosed without verification.

## 5. Failure handling

| Dependency | Failure | Behaviour |
|---|---|---|
| Ollama down / slow / wrong model | `CircuitBreaker` (3 failures → open for 30 s) | `route()` returns `None` immediately; agent shows the menu. Calls never wait on a dead LLM after the first few failures |
| LLM returns invalid JSON or off-schema intent | allow-list validation | treated as no answer |
| LLM reply contains advice / URL / digits | `guard_reply` returns `None` | falls back to the canned unclear reply |
| Speech server down | `VoiceUnavailable`, breaker | `/v1/calls/audio` and `/v1/synthesize` return `503 voice_unavailable`; the UI falls back to browser speech |
| Two callers want the same slot | `IntegrityError` on the unique index | loser is offered the next free slot with another doctor in the department, or told the slot was just taken |
| Session expired (15 min idle) | `UnknownCall` | `404 unknown_or_expired_call`; the stored transcript survives (admin-only to read) |
| SQLite contention | WAL + `busy_timeout=5000` | readers do not block the writer |

## 6. Observability

* `/metrics` (Prometheus): `hospicall_calls_started_total`, `hospicall_turns_total{intent,path}`,
  `hospicall_outcomes_total{outcome}`, turn-latency percentiles.
* `/v1/stats`: the same as JSON.
* `audit_event` table: append-only record of `emergency_escalation`, `recording_opt_out`, `verify_success`,
  `verify_failed` and `appointment_booked` / `appointment_rescheduled` / `appointment_cancelled`.
* Every `Intent` row stores the path (`rules` / `llm` / `fallback`) and latency, so "how often does the LLM have to
  rescue us?" is a SQL query. (Intent rows hold labels and slots, not raw text, so they are kept even after an opt-out.)

## 7. Evaluation methodology

Unit tests prove the code does what the author intended; **evals** check the agent does what a caller *needs*. They are
separate on purpose (`backend/evals/`), and the suite runs as a test (`tests/test_evals.py`) so a regression fails CI.

| Suite | What it is | Gate |
|---|---|---|
| Intent accuracy | 97 labelled utterances over 22 labels, including near-miss pairs (fee question vs booking, "real person" as identity vs "talk to a person" as human, "emergency department" vs "this is an emergency") | ≥ 95 % |
| Dialogues | 26 scripted multi-turn conversations with assertions on the text, intent, outcome and end-of-call flag of each turn (booking by department / doctor / time, privacy, cancel & reschedule, information, robustness) | 100 % |
| Safety red-team | 19 adversarial single turns: honest emergency wording, no dosage / diagnosis / prescription, prompt injection, SQL / HTML payloads, no URLs, no PHI before verification | 100 % |
| Latency | p50 / p95 over every turn of the dialogues | p95 ≤ 50 ms |

Judges are deterministic (substring / outcome assertions) so results are reproducible and free; the scenario file is
data, versioned with the code. `python -m evals.run_evals --llm` repeats the run with a live Ollama model in the loop to
measure how the fallback behaves, which is intentionally *not* a CI gate because model output is non-deterministic.

The first run of the suite failed 11 cases (intent 91.8 %, dialogue 24/26, safety 18/19). Real NLU defects were fixed
(fee questions routed to booking, "do not want to be recorded" unrecognised, "am I talking to a real person" routed to
the human-handoff intent, a self-harm label ordering bug in the harness, …) and a few scenario expectations were
corrected. That loop — write scenarios, find real bugs, fix, freeze as regression tests — is the point of having evals.

## 8. What would change at scale

* Replace `Scheduler`'s SQLite with a hospital information-system adapter (HL7 / FHIR); the interface is nine small
  methods (`departments`, `doctors`, `find_doctors_by_name`, `available_slots`, `patient_name`, `upcoming`, `book`,
  `cancel`, `reschedule`).
* PostgreSQL + Alembic; the partial unique index translates directly.
* Move `SessionManager` to Redis for multi-process deployment (it is a ~65-line in-memory class today).
* Telephony: a SIP/FreeSWITCH bridge calling `/v1/calls/audio`, with barge-in and VAD — the API is already turn-based.
* Add a held-out paraphrase set and track LLM-path accuracy separately from rules-path accuracy.
