# HospiCall — Architecture Test Answers

## 1. Complete Architecture

**What components exist?**
- Frontend: Patient call UI (phone), Hospital Admin Dashboard, Live Call Monitor, Analytics Dashboard
- Backend: API Gateway, Session Manager, Conversation Orchestrator, Appointment Service, Doctor Service, User Service, Notification Service, Audio Service
- Database: PostgreSQL (SQLite for demo), Redis cache, Object Storage
- AI/ML: Whisper (STT), Llama 3.1 8B via Ollama (LLM), Piper/Chatterbox (TTS), Intent Classifier
- Voice pipeline: VAD → STT → text buffer → intent → LLM → TTS → audio
- Integrations: FreeSWITCH telephony, calendar, SMS/email, hospital HIS
- Security: JWT auth, TLS, role-based access, audit logs
- Observability: JSON logging, monitoring dashboard, per-stage latency tracking

**Why does each component exist?**
- FreeSWITCH: bridges PSTN phone network to the internet (VoIP) — without it, no phone calls
- Whisper: converts patient speech to text so the AI can understand it
- Llama 3.1 8B: the "brain" — understands intent, makes decisions, generates responses
- Piper/Chatterbox: converts AI text responses back to speech the patient can hear
- Session Manager: keeps each call's conversation state isolated and consistent
- Appointment Service: handles booking, availability, conflict detection (transactional)
- Database: stores users, doctors, appointments, transcripts (durable memory)
- Redis: fast session state + hot data (availability) for speed
- Admin Dashboard: doctors/staff manage schedules and see call outcomes

**How do they communicate?**
- FreeSWITCH → backend: SIP/WebSocket audio streams
- Backend → Whisper/TTS: REST API (OmniVoice-Studio at localhost:3900)
- Backend → LLM: REST API (Ollama at localhost:11434)
- Backend → DB: SQL via ORM (SQLAlchemy)
- Backend → Redis: Redis protocol
- Frontend → Backend: REST + WebSocket
- Services between each other: internal REST / async message queue

**What happens when a request enters the system?**
A patient calls → FreeSWITCH accepts → session created → audio streamed → STT → intent → LLM → decision → DB ops → response text → TTS → audio back to caller.

---

## 2. Call Lifecycle — Every Step

1. Call initiation — FreeSWITCH accepts SIP session (<1s)
2. User identification — caller ID lookup by phone number (<50ms)
3. Greeting — TTS streams while parallel lookups run (200-500ms)
4. Speech capture — VAD detects speech, streams audio (real-time)
5. STT — Whisper converts to text (300-600ms streaming)
6. Intent detection — classify "book cardiologist tomorrow" (<200ms)
7. Context understanding — LLM enriches with user profile/history (300-800ms)
8. Decision-making — orchestrator routes to Appointment Service (<50ms)
9. Database ops — query cardiologists, check availability, rank (50-200ms)
10. Response generation — LLM drafts offer (400-1000ms)
11. TTS — stream response chunks (100-300ms to first audio)
12. Response delivery — audio streams to patient

**Total: ~1.5–3s to first meaningful response.**

**How do we make it feel instant?**
- TTS streaming (start speaking before full response ready)
- Partial STT (respond to full sentence, not whole turn)
- Parallel processing (pre-fetch availability while LLM thinks)
- Predictive intent (pre-load common data)
- Prompt caching
- Barge-in (interruptions stop TTS instantly)

---

## 3. Multi-User / Multi-Doctor Architecture

**15 doctors:** Single server, SQLite, one Ollama, one FreeSWITCH. Demo-grade.
**100 doctors:** Single beefy server, PostgreSQL, Redis, 5-10 concurrent calls.
**10,000 doctors:** Horizontally scaled microservices, load balancer, multiple trunks, GPU pool, sharded DB.

**Doctor profile stores:** identity, specialization, availability schedule, consultation timings, location, appointment rules, preferences.

**Appointment flow ("I want a cardiologist tomorrow"):**
AI understands requirement → searches doctors → checks availability → ranks → suggests options → user picks → books atomically → updates calendar → sends confirmation.

---

## 4. Data Architecture

**User data:** profile (id, phone, name, dob, insurance), preferences, history (appointments, calls, complaints).
**Conversation data:** full transcripts (speaker-labeled), AI responses, intents with extracted slots, sentiment, actions performed.
**Doctor data:** profiles, availability, schedules.
**Appointment data:** booking status, changes log, cancellations, reminders.

**Real-time:** active sessions, live availability, in-flight appointments (Redis hot).
**Historical:** transcripts, past calls/appointments, user history (PostgreSQL).
**Fast retrieval:** doctor schedules, appointment lookups, user-by-phone (indexed + cached).

---

## 5. AI Decision-Making

Components: STT, intent classifier, LLM, memory, retrieval, rule engine, safety layer.

**Answer directly:** greetings, FAQ, clarifications.
**Search database:** appointment requests, availability, doctor info.
**Call another service:** booking, notifications, calendar sync.
**Refuse:** medical advice, prescriptions, out-of-scope.
**Escalate to human:** emergency keywords, angry user, 2+ failed understanding attempts.

---

## 6. Memory Architecture

**Short-term (in-call):** last ~10 turns, current task state, extracted slots → Redis session, TTL 15 min.
**Long-term (across calls):** preferences, past appointments, key facts → PostgreSQL.

**Avoiding forgetting:**
1. Hydrate session with long-term memory at call start
2. Save full transcripts every call
3. Inject user context into LLM system prompt
4. Sliding window + summarize old turns when >10
5. Slot memory — ask only for missing info

---

## 7. Speed Optimization

Latency budget: STT 400ms + Intent 100ms + LLM 600ms + DB 100ms + TTS 400ms ≈ 1.6s total.

Techniques: streaming, parallel processing, Redis caching, load balancing, Q4 quantization + CPU threading (20 threads on i9-13900H), database indexing, async background jobs.

---

## 8. Failure Handling

| Failure | Response |
|---------|----------|
| LLM crashes | Scripted fallback, retry queue, alert |
| DB down | Redis read-only fallback, inform user, alert |
| Calendar fails | Manual availability fallback, flag for review |
| STT garbage | Ask to repeat; escalate after 2 fails |
| TTS fails | Pre-recorded fallback audio |
| Call drops | Clean session, log, no ghosts |
| Wrong AI decision | Confirmation required before booking; safety layer; human escalation |

**Principle: never silent. Every failure has a graceful user-visible response + log.**

---

## 9. Security Architecture

- PHI/PII encrypted at rest (AES-256), TLS in transit
- Audio access admin-only, retention policy (90 days default)
- Patient auth: caller ID + optional OTP
- Doctor/admin auth: JWT role-based
- Service-to-service: internal tokens + network isolation
- Audit log: every appointment action, every call, every sensitive data access — append-only

---

## 10. Timeline

**Total: 15 days** (buffer to 20)

| Phase | Days | Deliverable |
|-------|------|-------------|
| Foundation | 1–3 | FreeSWITCH + Ollama + OmniVoice verified; basic call works |
| Pipeline | 4–6 | FastAPI backend, session manager, SQLite, E2E wiring |
| Edge Cases | 7–9 | Barge-in, silence, hold, timeout, anger, error handling |
| Hospital Domain | 10–12 | Doctors, schedules, appointment booking flow |
| Frontend + Polish | 13–14 | Dashboard, live monitor, analytics, demo script |
| Testing + Wow | 15 | E2E tests, latency tuning, rehearsal |

**Top risk:** FreeSWITCH/SIP config — start on Day 1.

---

## Locked Decisions
- Title: **HospiCall**
- Scope: full hospital
- Brain: Llama 3.1 8B Q4 via Ollama (English only)
- STT: Whisper via OmniVoice-Studio
- TTS: Piper/Chatterbox via OmniVoice-Studio
- Telephony: FreeSWITCH + forwarding (9441321662)
- DB: SQLite demo → PostgreSQL production
- Backend: Python + FastAPI
- Cost: $0 demo
- Hardware: i9-13900H, 16GB RAM, Intel Iris Xe (CPU-only)
