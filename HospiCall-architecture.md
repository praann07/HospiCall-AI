# HospiCall — Hospital AI Voice Assistant
## Complete Technical Architecture & Engineering Plan

> Scope: Full hospital (not just dental). Zero-cost demo → funded production-scale.
> Phone number for demo: 9441321662
> Constraint: No code in this document — architecture, workflows, decisions only.

---

# 1. COMPLETE ARCHITECTURE

## Layer Diagram

```
[PATIENT / CALLER]
        │  Phone call
        ▼
┌─────────────────┐    ┌──────────────────────┐
│  PSTN           │    │  VIRTUAL NUMBER /    │
│  Phone Network  │───►│  CALL FORWARDING     │
└─────────────────┘    │  (9441321662)        │
                       └──────────┬───────────┘
                                  ▼
┌──────────────────────────────────────────────┐
│  TELEPHONY LAYER                             │
│  FreeSWITCH (SIP Server)                     │
│  - Call routing, session management          │
│  - Audio streaming to AI pipeline            │
│  - Barge-in detection (user interrupts AI)   │
└──────────────────┬───────────────────────────┘
                   ▼
┌──────────────────────────────────────────────┐
│  VOICE PROCESSING PIPELINE (Streaming)       │
│  STT (Whisper) → NLU (Intent) → LLM → TTS    │
└──────────────────┬───────────────────────────┘
                   ▼
┌──────────────────────────────────────────────┐
│  BACKEND SERVICES (FastAPI)                  │
│  - Session Manager                           │
│  - Conversation Orchestrator                 │
│  - Appointment Service                       │
│  - Doctor Service                            │
│  - Notification Service                      │
│  - User Service                              │
└───────┬──────────────┬──────────────┬────────┘
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ DATABASE     │ │ CACHE        │ │ INTEGRATIONS │
│ PostgreSQL   │ │ Redis        │ │ Calendar,    │
│ (SQLite for  │ │ - Session    │ │ SMS, Email,  │
│  demo)       │ │   state      │ │ Messaging    │
└──────────────┘ └──────────────┘ └──────────────┘
        │              │
        ▼              ▼
┌──────────────────────────────────────────────┐
│  OBSERVABILITY                               │
│  Logging / Monitoring / Alerting             │
└──────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1a. Frontend Layer
| Component | Why It Exists | Communication |
|-----------|---------------|---------------|
| **Patient Call UI** | Phone is the primary interface — frontend is minimal | Voice via PSTN |
| **Hospital Admin Dashboard** | Doctors/staff manage availability, appointments, view call transcripts | REST API + WebSocket |
| **Live Call Monitor** | Real-time view of active calls, transcripts streaming, status | WebSocket |
| **Analytics Dashboard** | Call volume, intent distribution, booking success rate, avg handling time | REST API |

### 1b. Backend Services
| Service | Responsibility |
|---------|----------------|
| **API Gateway** | Routes requests, auth check, rate limiting |
| **Session Manager** | Tracks active call sessions, conversation state, lifecycle |
| **Conversation Orchestrator** | The "director" — decides which service to call next in the dialogue |
| **Appointment Service** | CRUD for appointments, availability check, conflict detection |
| **Doctor Service** | Doctor profiles, specializations, schedules |
| **User Service** | Patient profiles, history, preferences |
| **Notification Service** | SMS/email confirmations, reminders |
| **Audio Service** | Bridges FreeSWITCH ↔ STT/TTS, manages audio streams |

### 1c. Database Layer
| Storage | Purpose |
|---------|---------|
| **Primary DB** | PostgreSQL (SQLite for demo) — relational data: users, doctors, appointments |
| **Cache** | Redis — session state, hot availability data, rate limits |
| **Object Storage** | Audio recordings, transcripts archive |

### 1d. AI/ML Components
| Component | Model | Purpose |
|-----------|-------|---------|
| **STT** | Whisper (via OmniVoice-Studio) | Speech → text |
| **LLM** | Llama 3.1 8B (Q4, via Ollama) | Intent understanding + response generation |
| **TTS** | Piper/Chatterbox (via OmniVoice-Studio) | Text → speech |
| **Intent Classifier** | Lightweight (can be LLM prompt or small classifier) | Fast routing before LLM |

### 1e. Voice Processing Pipeline
```
Streaming audio in → VAD (voice activity detection) → STT → text buffer
                 → intent classification → LLM reasoning → response text
                 → TTS → streaming audio out
```
All stages run **concurrently and stream** — never wait for full input/output.

### 1f. External Integrations
| Integration | Purpose |
|-------------|---------|
| **Telephony (FreeSWITCH)** | PSTN bridging, call control |
| **Calendar systems** | Doctor availability sync |
| **SMS/Email gateways** | Confirmations, reminders (free tier: Twilio trial / Email) |
| **Hospital HIS/EMR** | Patient records (future, when funded) |

### 1g. Authentication
- **Patient**: Phone-number based, no password needed for calls (natural auth — caller ID)
- **Doctor/Admin**: JWT tokens, role-based access
- **API-to-API**: Internal service tokens

### 1h. Logging
- Structured JSON logs per call (call_id, session_id, event, duration, latency per stage)
- Transcript archive with speaker labels
- Error logs with stack traces + context

### 1i. Monitoring
- Live dashboard: active calls, latency percentiles, error rates
- Alerts: STT failure, LLM timeout, DB down, call drops
- Per-stage latency tracking (STT time, LLM time, TTS time, total)

---

# 2. CALL LIFECYCLE — EVERY STEP

## Scenario: User calls, books a cardiology appointment tomorrow

| # | Step | What Happens | Est. Latency |
|---|------|-------------|--------------|
| 1 | **Call Initiation** | Patient dials → PSTN → forwarded → FreeSWITCH accepts, SIP session created | <1s |
| 2 | **User Identification** | Caller ID captured; session created in Redis; look up patient by number | <50ms |
| 3 | **Greeting** | TTS streams "Welcome to City Hospital..." while lookup runs in parallel | 200-500ms |
| 4 | **Speech Capture** | VAD detects speech, streaming audio to STT | real-time |
| 5 | **STT** | Whisper converts speech → text (streaming, partial results) | 300-600ms (streaming) |
| 6 | **Intent Detection** | "book cardiologist tomorrow" → intent=appointment_request, slot=tomorrow, dept=cardiology | <200ms |
| 7 | **Context Understanding** | LLM enriches: check user profile, past visits, session context | 300-800ms |
| 8 | **Decision-Making** | Orchestrator: intent = booking → call Appointment Service | <50ms |
| 9 | **Database Ops** | Query available cardiologists for tomorrow, check schedules, rank | 50-200ms |
| 10 | **Response Generation** | LLM generates offer: "Dr. Mehta is available tomorrow at 10 and 2. Which suits you?" | 400-1000ms |
| 11 | **TTS** | Stream response to speech, begin playing immediately | 100-300ms to first audio |
| 12 | **Response Delivery** | Audio streams to patient via FreeSWITCH | streaming |

**Total perceived latency for first meaningful response: ~1.5–3 seconds.** Acceptable for voice; target <2s for "instant" feel.

## Making Conversation Feel Instant
1. **TTS streaming** — start speaking the first chunk before the full response is generated
2. **Partial STT** — respond to a full sentence, not the full turn
3. **Parallel pipeline** — while AI speaks, pre-fetch next likely data (doctor availability)
4. **Predictive intent** — pre-load data for common intents (today's schedule)
5. **LLM prompt caching** — reuse system prompt/context across calls
6. **Audio barge-in** — user can interrupt; new input instantly stops TTS

---

# 3. MULTI-USER / MULTI-DOCTOR ARCHITECTURE

## Scale Tiers
| Scale | Architecture |
|-------|--------------|
| **15 doctors** | Single server, SQLite, one Ollama instance, one FreeSWITCH. Demo-grade. |
| **100 doctors** | Single beefy server or 2-3 VMs. PostgreSQL, Redis, N concurrency (5-10 concurrent calls). |
| **10,000 doctors** | Horizontally scaled. Microservices split, load balancer, multiple telephony trunks, GPU pool for LLM, sharded DB. |

## Doctor Profiles — What's Stored
- Identity (name, photo, contact)
- Specialization (cardiology, neurology, ...)
- Availability schedule (days, hours, recurring/one-off)
- Consultation timings & duration per visit type
- Location / clinic / floor
- Appointment rules (max per day, buffer between visits, new vs follow-up)
- Preferences (notification channel, auto-book allowed vs manual confirm)

## Appointment Workflow — "I want an appointment with a cardiologist tomorrow"
1. **AI understands** requirement (dept, time frame) via NLU
2. **Search doctors** — query doctor service: cardiologists active tomorrow
3. **Check availability** — cross-reference doctor schedule with existing bookings
4. **Rank doctors** — by availability, proximity to requested time, rating, patient history
5. **Suggest options** — LLM presents 2-3 options conversationally
6. **Book** — user picks a slot; appointment service atomically reserves it
7. **Update calendar** — sync to doctor's calendar, mark slot taken
8. **Send confirmation** — SMS/email to patient, notify doctor

## Concurrency Model
- Each call = one isolated session (session_id in Redis)
- Sessions are stateless to each other
- LLM is shared (loaded once in GPU memory, batched inference)
- Telephony capacity = FreeSWITCH config + provider trunk limit
- Appointment booking is transactional — no double-booking (DB constraint + atomic reservation)

---

# 4. DATA ARCHITECTURE

## Entities
| Entity | Fields | Type |
|--------|--------|------|
| **User (Patient)** | id, phone, name, dob, address, insurance, created_at | Historical + Fast lookup by phone |
| **User Preferences** | preferred doctor, language, reminder prefs, consent | Historical |
| **User History** | past appointments, past complaints, call history | Historical |
| **Conversation** | id, call_id, user_id, started, ended, outcome | Historical |
| **Transcript Segment** | id, conv_id, speaker (patient/ai), text, confidence, ts | Historical |
| **Intent** | id, conv_id, intent_type, extracted_slots (JSON), confidence | Historical |
| **Sentiment** | id, conv_id, segment_id, sentiment_label, score | Historical |
| **Action** | id, conv_id, action_type (booked/cancelled/escalated), payload JSON | Historical |
| **Doctor** | id, name, specialization, location, contact | Historical + Fast |
| **Doctor Schedule** | id, doctor_id, date, start, end, slot_duration | Real-time + Fast retrieval |
| **Appointment** | id, user_id, doctor_id, slot_start, slot_end, status, booked_at | Real-time |
| **Appointment Change Log** | id, appointment_id, old/new values, changed_at | Historical |
| **Reminder** | id, appointment_id, scheduled_for, sent_at, channel | Real-time + Historical |

## Data Classification
| Type | Examples | Storage Strategy |
|------|----------|------------------|
| **Real-time** | Active sessions, live availability, in-flight appointments | Redis (hot), DB (source of truth) |
| **Historical** | Transcripts, past calls, past appointments, user history | PostgreSQL, cold archive |
| **Fast retrieval** | Doctor schedules (tomorrow), appointment lookups, user by phone | Indexed columns + Redis cache |

---

# 5. AI DECISION-MAKING SYSTEM

## Components
| Component | Role |
|-----------|------|
| **STT** | Convert speech → text |
| **Intent Classifier** | Map text → intent (appointment, cancellation, reschedule, inquiry, emergency, human, out-of-scope) |
| **LLM (Llama 3.1 8B)** | Generate natural responses, handle context, slot filling |
| **Memory System** | Short-term session memory + long-term user memory |
| **Retrieval System** | Fetch doctors, availability, user history on demand |
| **Rule Engine** | Hard rules: emergency keywords → escalate; profanity → calm script; silence → timeout |
| **Safety Layer** | Refusals, medical disclaimers, never prescribe/give medical advice |

## When Should AI...?
| Action | Condition |
|--------|-----------|
| **Answer directly** | Greeting, FAQ, clarifications, status checks |
| **Search the database** | Appointment requests, availability, doctor info, user lookup |
| **Call another service** | Booking (Appointment Service), notifications, calendar sync |
| **Refuse** | Medical advice requests, prescriptions, anything outside scope |
| **Escalate to human** | Emergency keywords, repeated confusion, angry user, 2+ failed understanding attempts |

## Decision Flow
```
text → intent classifier
  → appointment → retrieval → LLM generation → TTS
  → inquiry → retrieval → LLM generation → TTS
  → emergency → rule engine → transfer to human immediately
  → out-of-scope → safety layer → refuse politely / escalate
```

---

# 6. MEMORY ARCHITECTURE

## Short-Term Memory (within a call)
- Current conversation turns (sliding window, last ~8-10 turns)
- Current task state (which flow: booking, cancellation, info gathering)
- Extracted slots so far (dept, date, time, doctor)
- Stored in **session context** in Redis, per session_id
- Expires when call ends (TTL 15 min post-call)

## Long-Term Memory (across calls)
- User preferences (preferred doctor, language, timing)
- Previous appointments
- Important facts (insurance provider, allergies — with consent)
- Stored in **PostgreSQL** user_profile / user_history tables

## Avoiding "Forgetting"
1. At call start, **hydrate** session with relevant long-term memory (last 3 visits, preferences)
2. Full transcript of every call saved to DB
3. LLM system prompt includes "You are speaking with [name], their preferred doctor is [X], last visit [Y]" 
4. Sliding window + summary: when >10 turns, summarize old turns and keep the summary
5. Slot memory: track filled slots; ask only for missing ones

---

# 7. SPEED OPTIMIZATION

## Latency Budget (target <2s to first response)
| Stage | Budget | Technique |
|-------|--------|-----------|
| STT | 400ms | Streaming whisper, partial results, faster-whisper engine |
| Intent | 100ms | Cache common intents, fast classifier before LLM |
| LLM | 600ms | Q4 quantized 8B, prompt caching, batched inference |
| DB | 100ms | Indexes (phone, doctor_id, date), Redis cache for hot schedules |
| TTS | 400ms | Pre-warm voice model, stream chunks, cache common phrases |
| **Total** | **~1.6s** | |

## Techniques
| Technique | Application |
|-----------|-------------|
| **Streaming** | TTS streams chunks; STT streams partial results — never wait for full turn |
| **Parallel processing** | While LLM thinks, pre-fetch availability; while TTS speaks, listen |
| **Caching** | Redis: doctor schedules (TTL 5 min), common greetings/phrases, intent→response maps |
| **Load balancing** | Multiple Ollama workers, FreeSWITCH channels; round-robin sessions |
| **Model optimization** | Q4 quantization, CPU threads tuned for i9-13900H (20 threads), AVX |
| **Database indexing** | phone, doctor_id, date, status indexes |
| **Background processing** | Confirmations, reminders, transcript archiving = async queue, not blocking call |

---

# 8. FAILURE HANDLING

| Failure | Detection | Response |
|---------|-----------|----------|
| **LLM crashes / times out** | Health check, timeout watchdog | Fallback scripted responses ("Let me connect you to our team"), queue for retry, alert |
| **Database down** | Connection pool errors | Serve from Redis cache (read-only), tell user "system is checking, please hold", alert immediately |
| **Calendar integration fails** | API error | Fall back to manual availability check, flag appointment for human review |
| **STT returns garbage** | Low confidence score | Ask user to repeat once; if still low, escalate to human |
| **TTS fails** | Engine error | Play pre-recorded fallback audio file |
| **FreeSWITCH drops call** | SIP disconnect event | Clean session, log, no ghost sessions |
| **Wrong AI decision** | Rule engine + human-in-the-loop | **Booking requires confirmation** — never auto-book without user's explicit "yes". Dangerous intents (medical advice) always refused. Escalation path always available. |
| **Model loaded but unresponsive** | Latency spike | Kill worker, spawn new, shift session to healthy worker |

## Design Principle
**Never silent.** Every failure has a user-visible graceful response. Every failure is logged with call_id for postmortem.

---

# 9. SECURITY ARCHITECTURE

## Data Protection
| Concern | Control |
|---------|---------|
| **User privacy** | PHI/PII encrypted at rest (AES-256), audio files access-controlled, retention policy (delete after 90 days default) |
| **Data in transit** | TLS everywhere, SIP over TLS/WSS |
| **Audio security** | Audio access = admin only, masked in logs, consent recorded |
| **Medical info** | Never logged in plaintext; access audited |

## Authentication & Authorization
| Actor | Method |
|-------|--------|
| **Patient** | Phone-number identity (caller ID) + optional OTP for account actions |
| **Doctor** | JWT login, role=doctor, can only access own schedule/appointments |
| **Admin** | JWT, role=admin, full access |
| **Service-to-service** | Internal bearer tokens, network isolation |

## Access Control Matrix
| Resource | Patient | Doctor | Admin |
|----------|---------|--------|-------|
| Own appointments | ✅ | ✅ (own) | ✅ |
| Doctor schedule | ❌ (via AI only) | ✅ (own) | ✅ |
| Transcripts | Own calls | Own calls | All |
| Patient records | Own | Own patients | All |
| System config | ❌ | ❌ | ✅ |

## Audit Logs
- Every appointment action (book/cancel/reschedule) — who, when, what
- Every call — call_id, phone, outcome
- Every admin access to sensitive data
- Immutable append-only audit table

---

# 10. TIMELINE (FIXED) — HOSPiCALL

## Total: 15 days (6–8 hrs/day, with buffer to 20)

| Phase | Days | Deliverable | Milestone |
|-------|------|-------------|-----------|
| **1. Foundation** | 1–3 | FreeSWITCH + SIP + call forwarding working; Ollama + Llama 3.1 8B verified; OmniVoice-Studio STT/TTS verified | A call comes in, AI greets, conversation happens (basic) |
| **2. Pipeline** | 4–6 | FastAPI backend; session manager; SQLite schema; FreeSWITCH→STT→LLM→TTS wired end-to-end | Full pipeline works, calls logged |
| **3. Edge Cases** | 7–9 | Interruption/barge-in, silence detection, hold message, timeout, anger/fallback, error handling all layers | System survives messy real-world calls |
| **4. Hospital Domain** | 10–12 | Doctor profiles, schedules, appointment booking flow, availability ranking, confirmation | "Book cardiologist tomorrow" works end-to-end |
| **5. Frontend + Polish** | 13–14 | Admin dashboard, live call monitor, analytics, demo script | Demo-ready, visually polished |
| **6. Testing + Wow** | 15 | Full E2E testing, latency tuning, bug fixes, rehearsal | "Wow, my father is astonished" |

## Key Risks & Mitigations
| Risk | Mitigation |
|------|------------|
| FreeSWITCH/SIP config eats days | Start Day 1, don't defer |
| CPU-only latency feels slow | Tuning budget in Phase 6; streaming everywhere; script short responses |
| Demo fails live | Rehearsed script, pre-warmed models, fallback scripted responses |
| Booking bugs (double-book) | Transactional booking + unique constraint from day 1 |
| LLM hallucination (medical) | Rule engine + safety layer + confirmation required before booking |

## Demo-Ready Definition
Caller dials 9441321662 → greeted by name if known → asks for cardiologist tomorrow → AI checks availability → offers options → caller picks → AI books + confirms + sends SMS → admin dashboard shows the call, transcript, and booking. All on the laptop, zero cost.

---

# DECISIONS LOCKED
- **Title**: HospiCall
- **Scope**: Full hospital
- **Brain**: Llama 3.1 8B (Q4) via Ollama — English only, no multilingual overhead
- **STT**: Whisper via OmniVoice-Studio
- **TTS**: Piper/Chatterbox via OmniVoice-Studio
- **Telephony**: FreeSWITCH + call forwarding (9441321662)
- **DB**: SQLite (demo) → PostgreSQL (production)
- **Backend**: Python + FastAPI
- **Frontend**: Admin dashboard (React or plain HTML/JS)
- **Cost**: $0 for demo; funded for production scale
- **Hardware**: i9-13900H, 16GB RAM, Intel Iris Xe (CPU-only)
