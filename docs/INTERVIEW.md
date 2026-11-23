# Interview guide — HospiCall AI

Everything here is true of the code in this repository. Numbers can be reproduced with the commands in the README.

## 30-second pitch

> HospiCall is a self-hosted AI receptionist for hospitals. A caller — by browser voice or text — can book, cancel and
> reschedule appointments and ask about hours, location and fees. The design point is safety: a deterministic agent makes
> every decision and every database write, an optional local LLM only helps with wording, and the system is honest in
> emergencies about being automated. It has 406 tests at 97 % coverage plus a separate evaluation suite — 100 % intent
> accuracy on 97 labelled utterances, 26/26 multi-turn dialogues, 19/19 safety red-team cases — enforced in CI.

## 3-minute demo script

1. `uvicorn app.main:app` → open <http://localhost:8000>. Point at the status badges (database / LLM / voice).
2. **Start call.** Note the greeting: it says *automated AI assistant* and *calls are transcribed* — consent by design.
3. Type **"book a cardiologist tomorrow morning"** → it offers real free slots (not made-up ones) → pick **"2"** →
   name (say **"skip"**) → it reads the booking back → **"yes"** → booked. *"Notice it asked for confirmation before writing."*
4. Type **"cancel my appointment"** → it will not reveal anything until you give the last four digits of the phone number
   (`1662` for the demo patient `9441321662`). Try a wrong code first. *"Three wrong tries end the verification; the codes
   are stored as `####` in the transcript."*
5. Type **"I have severe chest pain"** → the reply tells you it is an automated assistant that cannot send help and to
   call 108. *"I deliberately don't pretend a human is coming."*
6. Type **"what medicine should I take for a headache"** → refuses, offers an appointment.
7. Type **"please don't record this"** → fetch `/v1/calls/<id>/transcript` with the `X-API-Key` header (start the server
   with `HOSPICALL_ADMIN_API_KEY=demo`) and show it is empty — it also erased what was already saved.
8. Open `/docs` (OpenAPI) and `/metrics` (Prometheus) to show it is production-shaped.
9. Finish with `python -m evals.run_evals` — one second, PASS table.

## Design questions you should expect

**Why not just give an LLM tools and let it run the conversation?**
Because the cost of a wrong action is asymmetric. A mis-booked slot or a confidently wrong medical statement is worse
than an awkward sentence. So rules own decisions and writes; the LLM is a router/paraphraser behind a JSON schema, its
proposed intent is re-checked by the same gates (verification, confirmation), and its text passes an output guard. As a
side-effect the common path takes ~1 ms and needs no GPU.

**What happens when the LLM is down?**
A circuit breaker (3 failures → open for 30 s) makes the call return immediately and the agent shows a short menu. I
tested exactly that with `httpx.MockTransport`, and I saw it live: the configured model wasn't installed on my machine,
Ollama returned 404, and the agent degraded to the menu instead of crashing.

**How do you prevent double booking?**
Not with an `if` check — that races. A *partial unique index* on `(doctor_id, slot_start) WHERE status='booked'`
makes the database the arbiter; the scheduler books inside a savepoint, catches `IntegrityError`, and tries the next
doctor in the department. There is a test where 8 threads race for one slot on a file database and exactly one wins.
Reschedule inserts the new slot before cancelling the old one in one transaction, so a failure never leaves the patient
with no appointment.

**How do you handle privacy?**
Identity = last four digits of the caller's number before any PHI (appointment list / cancel / reschedule). The opening
greeting is generic — no name or appointment detail is spoken before verification. Attempted codes and any 7+ digit run
are masked in stored transcripts. The greeting discloses AI and transcription; "don't record" stops saving and deletes
what was saved. Transcripts and admin endpoints are off by default, key-protected, constant-time compared, and mask
phone numbers. The HTTP layer also has body-size caps, per-route rate limits, and a strict CSP. All data is synthetic. The limitation I'd call out myself: last-4 is only as strong as the caller ID, which
real telephony provides and the browser demo does not.

**How do you know it works?**
Three layers. (1) 406 unit/integration tests, including concurrency and every HTTP error path. (2) A separate eval
suite that measures what a caller needs: intent accuracy, scripted dialogues, a safety red-team, latency percentiles —
with gates that fail CI. (3) A live run against a real uvicorn server. The first eval run failed 11 cases; fixing them
found real bugs, like "how much is the consultation fee" being treated as a booking request.

**Why regex NLU instead of a model?**
Explainable, instant, no training data, and it hits 100 % on the labelled set. The honest weakness is paraphrases it has
never seen — which is exactly what the LLM fallback handles, and what I'd measure next with a held-out paraphrase set.

**Hardest bug?**
Two good ones. (a) The original name extraction turned "I am looking for a doctor" into the patient name "Looking" —
now names only come from explicit phrasing. (b) The original `/calls/audio` endpoint reset the session on every turn and
used the caller-supplied `call_id` in a file path (path traversal); call IDs are now server-generated and
regex-validated, and the temporary audio is deleted after use.

**What would you do with more time?**
SIP/FreeSWITCH with barge-in and VAD; an HL7/FHIR scheduler adapter (the interface is nine methods); Postgres + Alembic;
Redis sessions; Hindi/Telugu; and a held-out paraphrase eval that tracks LLM-path accuracy separately.

## Limitations — say these before they ask

* Not connected to a real phone line or a real hospital information system.
* English only.
* Rules NLU: unseen paraphrases fall to the LLM or the menu.
* Caller identity is caller-ID + last four digits; fine for a demo, not a substitute for real authentication.
* The LLM path is not part of the deterministic CI gate (non-deterministic output); it has its own opt-in eval flag.
* Demo data is synthetic.

## Numbers to remember

| | |
|---|---|
| Tests | 406 passing, 97 % line coverage |
| Intent accuracy | 100 % (97 utterances, 22 labels; gate ≥ 95 %) |
| Dialogues / safety | 26/26 · 19/19 |
| Latency | p50 ≈ 0.6 ms, p95 ≈ 3.4 ms on the rules path |
| Concurrency test | 8 threads, 1 slot → exactly 1 booking |
| Stack | FastAPI, SQLAlchemy 2, SQLite WAL, Pydantic v2, httpx, pytest, ruff, Docker, GitHub Actions |

## Commands

```bash
cd backend
uvicorn app.main:app --reload       # run
pytest --cov=app                    # tests
python -m evals.run_evals           # agent evaluation (add --llm for live Ollama)
ruff check app scripts tests evals  # lint
```
