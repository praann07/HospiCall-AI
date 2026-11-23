# HospiCall — Drawbacks Self-Assessment (Completed)

> Context: This is a **zero-cost demo** (15-day build, laptop hardware, solo dev) targeting funding, NOT production. Ratings reflect demo-acceptability plus production-readiness where stated.
> Scale: Severity (Critical/High/Medium/Low) · Likelihood (Certain/Likely/Possible/Unlikely) · Status (Acknowledged/Mitigated/Open/Unknown)

---

## Section A: Technical Architecture

### A1. Telephony & Voice Pipeline

**A1.1 FreeSWITCH complexity (2–4 days for SIP config)**
- Severity: **High** | Likelihood: **Certain** | Status: **Mitigated**
- Acknowledged as top timeline risk. Mitigation: start Day 1, use a Dockerized FreeSWITCH with a proven config template, and cap debugging time — if SIP integration exceeds 3 days, fall back to a **simulated call path** (dial-in via SIP client on the same machine) for the demo while keeping the real PSTN path as stretch.

**A1.2 Single point of failure — no failover**
- Severity: **High** | Likelihood: **Likely** (in production) | Status: **Acknowledged**
- Acceptable for a demo. Production needs: 2 FreeSWITCH nodes behind a SIP load balancer, failover route to the PSTN. Documented as production requirement.

**A1.3 Barge-in detection / echo cancellation not detailed**
- Severity: **High** | Likelihood: **Certain** (will hit it) | Status: **Open**
- Not yet implemented/detailed. Must use VAD + energy detection on the inbound audio while TTS streams; abort TTS on speech start. Echo cancellation: FreeSWITCH supports AEC via mod_sofia/echo cancellation modules. **Open item — needs spike testing in Phase 3.**

**A1.4 Audio quality on PSTN (8 kHz mono, noisy)**
- Severity: **High** | Likelihood: **Certain** | Status: **Mitigated**
- Whisper handles 8 kHz but with reduced accuracy. Mitigation: test with real calls early (Phase 1), use Whisper's `--language en` + temperature fallback for robustness, and design responses to tolerate mis-transcription (confirm critical details like names/times back to the user).

**A1.5 Caller ID spoofing → unauthorized patient data access**
- Severity: **Critical** | Likelihood: **Possible** | Status: **Open**
- Real risk even in demo if real PII is loaded. Mitigation: **never auto-reveal sensitive data on caller ID alone** — verify identity (DOB, OTP, or known info) before sharing PHI. In the demo, use synthetic patient data. **Open item — verification flow must be designed before any real data is used.**

**A1.6 No codec fallback**
- Severity: **Low** | Likelihood: **Unlikely** (demo on same machine) | Status: **Acknowledged**
- Configure FreeSWITCH with a codec negotiation list (Opus preferred, G.711 fallback). FreeSWITCH handles negotiation natively.

**A1.7 One-way streaming assumption / no jitter buffer**
- Severity: **Medium** | Likelihood: **Possible** (real network) | Status: **Acknowledged**
- Demo runs loopback (no jitter). Production requires jitter buffer + PLC. Not a demo blocker.

### A2. AI/ML Stack

**A2.1 Llama 3.1 8B CPU-only — 600ms target unrealistic**
- Severity: **High** | Likelihood: **Certain** | Status: **Mitigated**
- Correct: realistic CPU inference on i9-13900H ≈ 1.5–3s/token for 8B Q4. Mitigation: this is the **biggest honest fix** — shift the architecture from "LLM generates every response" to **template-based responses for fixed flows** (booking, availability, confirmations) with the LLM only for free-form understanding and fallback. This slashes perceived latency to ~0.8–1.2s for scripted paths. LLM latency accepted where unavoidable (2–3s is tolerable on phone if TTS streams first).

**A2.2 English-only excludes 22+ Indian languages**
- Severity: **High** | Likelihood: **Certain** (for real India hospitals) | Status: **Acknowledged**
- Deliberate demo constraint (locked decision). Acknowledged as the #1 production gap. Production path: swap Ollama model for a multilingual one (Qwen 2.5 / IndicBART) + multilingual Whisper. Flagged for funding pitch.

**A2.3 No medical fine-tuning → hallucination risk**
- Severity: **Critical** | Likelihood: **Likely** | Status: **Mitigated**
- The safety layer + rule engine **refuses** all medical advice/prescriptions by design. Demo scope = scheduling + FAQs only, with a strict "I'm not able to give medical advice" script. Validation set: list of ~20 canned doctor-name/drug-name phrases to test NER accuracy. **Production: domain fine-tune required.**

**A2.4 Whisper hallucinates on silence**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Open**
- Mitigation: strict VAD gates STT input (only send segments with real speech energy); ignore zero-confidence/low-energy transcriptions; set `condition_on_previous_text=false`. **Open item — must be tested in Phase 3.**

**A2.5 Context window on long calls (>30 turns)**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Llama 3.1 8B context = 8K–128K (Ollama default 8K is enough for a ~30-turn call). Sliding window + running summary implemented at the orchestrator level. Acceptable for demo call lengths.

**A2.6 No intent confidence thresholding**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Add threshold: if intent confidence < 0.4 → "I didn't catch that, could you repeat?"; < 0.6 → confirm with user. Simple to add in Phase 3.

**A2.7 Sentiment analysis in schema but not pipeline**
- Severity: **Low** | Likelihood: **Likely** | Status: **Open**
- Deferred. Simplest use: LLM classifies sentiment per turn (positive/neutral/negative) and triggers escalation on sustained negative. Add only if Phase 6 has time. Not demo-critical.

**A2.8 No model evaluation framework / A/B testing**
- Severity: **Medium** | Likelihood: **Certain** (will need) | Status: **Acknowledged**
- Demo: manual golden-set testing (20 scripted scenarios). Production: evaluation harness required before model swap. Switching cost is low in Ollama (model name is config), so this is manageable.

### A3. Backend & Database

**A3.1 SQLite single-writer lock → booking deadlock risk**
- Severity: **High** | Likelihood: **Likely** (2 concurrent calls) | Status: **Mitigated**
- Real risk. Mitigation for demo: set SQLite `busy_timeout` + WAL mode; serializing writes through a single async task. Demo rarely has 2 simultaneous bookings, but the code path is designed to be transactional. **Production: PostgreSQL (locked decision).**

**A3.2 PostgreSQL migration not designed upfront**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Mitigation: use SQLAlchemy ORM from Day 1 with a schema that maps cleanly to Postgres (proper FKs, indexed columns designed now, JSONB-ready fields). Migration = swap engine string + minor type fixes.

**A3.3 Redis dependency — session loss if Redis dies**
- Severity: **High** | Likelihood: **Possible** | Status: **Open**
- Demo: single-machine, Redis failure = FreeSWITCH still holds the call; implement fallback that writes session to in-memory dict (same process) so call continues; log + alert. Persistence: enable RDB snapshots + AOF in production. **Open item.**

**A3.4 No API versioning**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Add `/v1/` prefix from Day 1 (cheap now, painful later).

**A3.5 No rate limiting (SMS/email free tiers)**
- Severity: **Medium** | Likelihood: **Certain** (free tiers limit) | Status: **Acknowledged**
- Add per-user rate limit (e.g., max 3 SMS/day) + queue. Demo uses confirmations sparingly.

**A3.6 No database backup strategy**
- Severity: **Critical** (production) | **Low** (demo) | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: SQLite file copied to OneDrive/cloud daily (zero cost). Production: automated encrypted Postgres backups + offsite storage. Legal liability acknowledged.

**A3.7 15-min session TTL vs 20-min hold**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Mitigation: TTL refreshes on any activity; hold state extends TTL; 20-min hold triggers a graceful close before state loss. Easy fix.

**A3.8 Analytics on same DB slows bookings**
- Severity: **Low** (demo) | **High** (production) | Likelihood: **Unlikely** (demo scale) | Status: **Acknowledged**
- Production: read replica for analytics. Not a demo concern.

---

## Section B: Functional / Domain

### B1. Scope & Features

**B1.1 Emergency handling depth ("escalate to human" — who picks up?)**
- Severity: **Critical** | Likelihood: **Certain** | Status: **Mitigated**
- Real gap. Mitigation for demo: emergency keywords → AI gives hospital emergency number + asks to hold; **no guarantee of a live human** — clearly scripted, honest. Production: after-hours routing to hospital emergency desk. The demo script avoids promising a human that doesn't exist.

**B1.2 No prescription/refill flow (>30% of hospital calls)**
- Severity: **Medium** | Likelihood: **Certain** (real hospitals) | Status: **Acknowledged**
- Deliberately out of scope for demo. Acknowledged explicitly in pitch as Phase-2 feature.

**B1.3 No lab report / test result delivery**
- Severity: **Medium** | Likelihood: **Certain** (real hospitals) | Status: **Acknowledged**
- Out of scope for demo. Flagged as production roadmap item.

**B1.4 No insurance verification**
- Severity: **Medium** | Likelihood: **Likely** (real hospitals) | Status: **Acknowledged**
- Demo: "reports to hospital for insurance checks" script. Out of scope.

**B1.5 No multi-department coordination**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo handles single-department booking only. Acknowledged limitation.

**B1.6 No patient follow-up calls**
- Severity: **Low** | Likelihood: **Likely** | Status: **Acknowledged**
- Out of scope for demo; natural Phase-2 feature.

**B1.7 No second-opinion flow**
- Severity: **Low** | Likelihood: **Possible** | Status: **Acknowledged**
- Out of scope.

**B1.8 Pediatric/geriatric speech patterns**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Whisper degrades on child/elderly speech. Demo: script uses adult speakers. Flagged.

**B1.9 Hearing/speech-impaired exclusion**
- Severity: **High** (ethical) | **Low** (demo) | Likelihood: **Certain** | Status: **Acknowledged**
- Phone-voice-only excludes this population. Production: IVR/text/relay options. Documented as an ethical gap, not fixed in demo.

**B1.10 Call recording consent flow**
- Severity: **High** | Likelihood: **Certain** | Status: **Open**
- Legal requirement in many jurisdictions. Mitigation: AI opens every call with "This call may be recorded for quality..." and stores consent flag in the call record. **Open item — must be in the greeting script from Day 1.**

### B2. Doctor & Schedule

**B2.1 Calendar sync not detailed (which calendar? OAuth?)**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Open**
- Demo: schedule stored in DB only, no external calendar. Production: Google/Outlook/iCal OAuth — documented as integration work, not demo scope.

**B2.2 Doctor-absent → schedule update propagation**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: admin dashboard edits schedule; ongoing calls read current schedule at booking time (always fresh). Adequate for demo.

**B2.3 Rating source for "rank by rating" undefined**
- Severity: **Low** | Likelihood: **Certain** | Status: **Mitigated**
- Demo ranking: availability + slot time proximity only. Rating column exists but defaulted to neutral. Honest fix — don't show fake ratings.

**B2.4 No waitlist feature**
- Severity: **Low** | Likelihood: **Possible** | Status: **Acknowledged**
- Out of scope. Phase-2.

**B2.5 Overbooking prevention rules not engine-defined**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: simple slot model (fixed slot duration, no overlap allowed, DB unique constraint on doctor+slot). Enough for demo. Production: full scheduling engine.

**B2.6 Single-hospital assumption**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Multi-tenant hospital_id column designed into schema from Day 1 (cheap now), but routing across chains is out of scope.

**B2.7 Doctor notification preferences undefined**
- Severity: **Low** | Likelihood: **Possible** | Status: **Acknowledged**
- Demo: single default channel (SMS). Preferences column exists.

### B3. Patient Experience

**B3.1 No multilingual support (biggest gap)**
- Severity: **Critical** (production) | **High** (demo credibility) | Likelihood: **Certain** | Status: **Acknowledged**
- Locked out of demo scope. This is the #1 thing investors/hospitals will challenge. Prepare a defensible answer + roadmap.

**B3.2 No IVR menu fallback**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Demo: if AI fails repeatedly, script ends call gracefully with hospital phone hours. A DTMF IVR fallback is cheap to add in FreeSWITCH — defer.

**B3.3 No callback scheduling ("call me back")**
- Severity: **Low** | Likelihood: **Possible** | Status: **Acknowledged**
- Out of scope.

**B3.4 Complaint/escalation channel staffed?**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Same as B1.1 — demo does NOT promise a live human. Script is honest. Production: staffed escalation.

**B3.5 Reschedule workflow underdefined**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo supports book + cancel; reschedule = cancel + rebook (simplified). Documented.

**B3.6 Family/proxy booking (caller ID mismatch)**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: ask "Whose appointment is this?" and collect patient name/phone verbally. Identity not tied to caller ID for booking (only for data lookup, and sensitive data requires verification per A1.5).

---

## Section C: Security & Compliance

**C1 PHI/PII compliance (HIPAA/DISHA/ABDM)**
- Severity: **Critical** (production) | Likelihood: **Certain** | Status: **Acknowledged**
- Demo uses **synthetic data only** — no real patient PHI, avoiding compliance exposure in the demo. Production compliance (ABDM in India) is a documented prerequisite before real deployment. **Non-negotiable boundary.**

**C2 Audio retention (90 days vs 5+ year legal requirement)**
- Severity: **High** | Likelihood: **Certain** | Status: **Open**
- 90-day default conflicts with medical record retention laws. Demo: synthetic data, no legal exposure. Production: retention policy must match jurisdiction. **Open item.**

**C3 Consent recording auditable?**
- Severity: **High** | Likelihood: **Likely** | Status: **Open**
- Verbally captured at call start + stored as boolean with timestamp in call record. Not yet built into the audio archival flow. **Open item.**

**C4 No penetration testing plan**
- Severity: **High** (production) | **Low** (demo) | Likelihood: **Possible** | Status: **Acknowledged**
- Demo: loopback-only, no external exposure. Production: pentest + SIP hardening before any real deployment.

**C5 No GDPR / DPDP Act review**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Demo avoids this via synthetic data. Production requires DPDP compliance (right to erasure, portability, consent). Documented prerequisite.

**C6 Internal bearer tokens — network actually isolated?**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Open**
- Demo: all services on localhost, loopback only. Production: firewall rules + VPC isolation to be defined. **Open item.**

**C7 SQL injection — explicit prevention**
- Severity: **Medium** | Likelihood: **Unlikely** | Status: **Mitigated**
- Using SQLAlchemy ORM (parameterized by default) from Day 1. Code review checklist covers raw-SQL bans.

**C8 Data residency guarantees**
- Severity: **Low** (demo) | **High** (production) | Likelihood: **Possible** | Status: **Acknowledged**
- Production hosting region TBD; India data sovereignty requires in-country hosting. Documented.

**C9 Audit trail protected from admin deletion?**
- Severity: **High** | Likelihood: **Possible** | Status: **Open**
- Append-only audit table (DB-level trigger denying UPDATE/DELETE) + separate admin from auditor role. **Open item — implement before production.**

---

## Section D: Operational

### D1. Scalability

**D1.1 "10,000 doctors" tier is handwaved**
- Severity: **Medium** | Likelihood: **Likely** (if funded) | Status: **Acknowledged**
- This document is a demo plan. Scaling tier is directional only. Production requires a dedicated architecture pass. Be honest in the pitch: "designed to scale, not yet proven at scale."

**D1.2 Single LLM = no horizontal AI scaling**
- Severity: **High** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: one Ollama. Production: model pool behind a router. Direction documented.

**D1.3 FreeSWITCH concurrent-call capacity undocumented**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Unknown**
- Needs load testing. Estimate: single laptop handles ~10–30 concurrent calls before CPU contention. Measure in Phase 6.

**D1.4 No load testing (Monday-morning spike)**
- Severity: **High** (production) | **Low** (demo) | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: 1–2 concurrent calls max. Production: load test with k6/sipp before launch.

**D1.5 Storage growth (5GB/day at 1000 calls/day)**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Demo: negligible. Production: tiered storage (hot→cold→archive), compression, retention policy.

### D2. Reliability

**D2.1 No SLA targets defined**
- Severity: **High** (production) | **Low** (demo) | Likelihood: **Certain** | Status: **Acknowledged**
- Demo has no SLA. Production needs uptime/latency/error-rate commitments. Documented as pre-launch requirement.

**D2.2 No disaster recovery plan**
- Severity: **High** | Likelihood: **Possible** | Status: **Acknowledged**
- Demo: laptop + daily DB file copy. Production: offsite backups + recovery runbook.

**D2.3 No chaos testing**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Not demo scope. Production: simulate Redis/DB/LLM failure during calls (Phase 3 already covers graceful handling — extend to automated tests).

**D2.4 Single region deployment**
- Severity: **Low** (demo) | **High** (production) | Likelihood: **Unlikely** (demo) | Status: **Acknowledged**
- Not applicable at demo scale. Documented production direction.

### D3. Observability

**D3.1 No monitoring stack chosen**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Mitigated**
- Demo: **Prometheus + Grafana** (free, local, lightweight) collecting per-stage latency + error counters. Fixed choice now — cheap and standard.

**D3.2 No alert escalation policy**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo: console alerts only, no paging. Production: pager/escalation matrix required for 24/7 hospital.

**D3.3 No on-call rotation**
- Severity: **High** (production) | **Low** (demo) | Likelihood: **Certain** | Status: **Acknowledged**
- Not applicable to demo (non-critical, best-effort). Production requires 24/7 on-call — acknowledged as a real operational requirement.

---

## Section E: Business & Product

**E1 No monetization model**
- Severity: **Critical** (for funding) | Likelihood: **Certain** | Status: **Acknowledged**
- Proposed models: per-clinic SaaS subscription (primary), per-call usage (secondary). Needs validation with pilot hospitals. Prepared slide, not yet tested with buyers.

**E2 No customer discovery evidence**
- Severity: **Critical** | Likelihood: **Certain** | Status: **Acknowledged**
- Biggest business risk. The pitch must include a plan for 10 discovery interviews with hospital admins before claiming validation.

**E3 No competitor analysis**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Known players: SoundHound, Nuance, Hippocratic AI, Infermedica, plus Indian players. Differentiation = local language + on-prem/zero-data-leave + open-source cost. Must produce a competitive matrix for the pitch.

**E4 Regulatory pathway undefined (FDA/CDSCO/medical device)**
- Severity: **Critical** (production) | Likelihood: **Certain** | Status: **Acknowledged**
- Demo (scheduling only, no medical advice) is low-risk software, likely not a medical device. If scope grows to advice/prescriptions, classification changes. Documented, needs legal review before real deployment.

**E5 No go-to-market strategy**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Hospital procurement cycles are 6–18 months. Strategy: land a pilot via a personal hospital contact, not cold sales.

**E6 No pilot hospital partnership**
- Severity: **Critical** | Likelihood: **Certain** | Status: **Acknowledged**
- The demo is the vehicle to secure this. Need a letter of intent target list.

**E7 No clinical validation**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Prepared answer: "This is a scheduling/communication system, not a clinical decision system." Administrative (non-clinical) positioning avoids this burden.

**E8 No liability model**
- Severity: **Critical** | Likelihood: **Certain** | Status: **Acknowledged**
- AI books wrong appointment → liability unclear. Mitigation: booking requires explicit user confirmation + audit trail + contract terms allocating responsibility. Legal review required before production.

**E9 No HIS/EMR integration**
- Severity: **Critical** (production adoption) | **Low** (demo) | Likelihood: **Certain** | Status: **Acknowledged**
- Hospitals won't buy without HIS/EMR integration. Demo: standalone booking in DB. Production: HL7/FHIR integration = major roadmap item.

**E10 No pricing page / ROI calculator**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Acknowledged**
- Build simple ROI calc: cost per call (AI) vs receptionist, saved hours, no missed calls. Include in pitch deck.

---

## Section F: Team & Timeline

**F1 15-day timeline aggressive, zero buffer**
- Severity: **High** | Likelihood: **Likely** | Status: **Mitigated**
- Buffer to 20 days stated. Prioritization rule: **core booking flow works first**; everything else is cuttable. Demo must not depend on perfect conditions.

**F2 Single point of knowledge (bus factor = 1)**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Mitigation: document everything in this plan + runbook as you build; keep configs in git. Acceptable for demo.

**F3 No testing strategy in timeline (1 day "Testing + Wow")**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Reality: lightweight tests inline per phase (Phase 2: pipeline smoke test; Phase 3: edge-case scenario script; Phase 4: booking correctness test; Phase 6: golden-set + 3 live demo rehearsals). Not formal CI, but test coverage exists.

**F4 Demo script dependency = works only in perfect conditions**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Countermeasure: rehearse the **messy** scenarios too (interrupt, angry, silent, wrong info) so failures look handled, not broken.

**F5 No rollback plan**
- Severity: **Medium** | Likelihood: **Possible** | Status: **Acknowledged**
- Use git; tag each phase; keep last working config snapshot. One-command restore for DB + config.

**F6 Scope creep risk**
- Severity: **High** | Likelihood: **Certain** | Status: **Mitigated**
- Feature freeze after Phase 2. Change control = "add to roadmap, don't add to demo." This doc is the contract.

**F7 Demo failure mode in front of investors unclear**
- Severity: **High** | Likelihood: **Possible** | Status: **Open**
- Plan: if AI hallucinates/mishears live, say "that's exactly the edge case we're handling" and show the fallback path. Rehearse this. **Open item — write the recovery script.**

---

## Section G: User Research & Validation

**G1 No user testing with real patients/doctors/staff**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Post-demo plan: 5 interviews (1 doctor, 1 receptionist, 3 patients incl. elderly). Not demo-blocking.

**G2 No accessibility testing (WCAG, deaf users)**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Phone-voice excludes deaf users (see B1.9). Acknowledged as product gap.

**G3 No persona definitions**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Acknowledged**
- Quick personas: Elderly patient (calls for bookings, slow speech, needs confirmation), Working adult (fast, wants SMS confirm), Receptionist (uses dashboard), Doctor (checks/updates schedule). Used to guide demo script.

**G4 No user journey mapping**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- Demo covers partial journey (call→book→confirm). Full lifecycle (pre-call, no-show, follow-up, feedback) mapped in roadmap.

**G5 No success metrics defined**
- Severity: **High** | Likelihood: **Certain** | Status: **Open**
- Define now: booking success rate ≥ 80% on demo script, ≤2 confirmation errors, latency ≤3s p90, 100% of calls logged. **Open item — finalize numbers in Phase 6.**

**G6 No failure metrics / thresholds**
- Severity: **High** | Likelihood: **Certain** | Status: **Open**
- Thresholds: hallucination/medical-advice count = 0 (hard zero), booking error rate < 2%, mis-transcription that changes meaning < 10%. **Open item.**

---

## Section H: Documentation Gaps

**H1 No data flow diagram (field-level)**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Open**
- Add: field-level flow for the booking request (phone→session→intent slots→doctor query→appointment row). **Add to architecture doc.**

**H2 No ER diagram**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Open**
- Produce ER diagram from the entity tables (FKs: appointment→user/doctor, transcript→conversation, etc.). **Add.**

**H3 No sequence diagrams**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Open**
- Produce 3: (1) happy-path booking, (2) interruption, (3) failure/escalation. **Add.**

**H4 No deployment diagram**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Open**
- One page: laptop runs FreeSWITCH + FastAPI + Ollama + OmniVoice + Redis + SQLite; ports; loopback. **Add.**

**H5 No CI/CD pipeline**
- Severity: **Low** (demo) | **High** (production) | Likelihood: **Certain** | Status: **Acknowledged**
- Demo: git + manual run. Production: GitHub Actions (lint, tests, build, deploy). Documented.

**H6 No versioning strategy (API/model/schema)**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Acknowledged**
- API `/v1`, model pinned by Ollama tag, schema via Alembic migrations from Day 1. Documented.

**H7 No feature flags**
- Severity: **Low** | Likelihood: **Possible** | Status: **Acknowledged**
- Demo: config flags in a single settings file (simulate call vs real call, use LLM vs template). Cheap now.

**H8 No configuration management / secrets**
- Severity: **High** | Likelihood: **Certain** | Status: **Acknowledged**
- Demo: `.env` file, gitignored. Production: secrets manager (Vault/cloud KMS). Never hardcode. Documented.

**H9 No i18n framework**
- Severity: **Medium** | Likelihood: **Likely** | Status: **Acknowledged**
- English-only now (locked). Design responses via a string-template layer so multilingual is a swap later, not a rewrite.

**H10 No documentation plan**
- Severity: **Medium** | Likelihood: **Certain** | Status: **Acknowledged**
- README (setup), API docs (auto from FastAPI), runbook (start/stop/restore), this architecture doc. Written incrementally during build, not at the end.

---

## Top 10 Critical Items (Priority Order)
1. **Synthetic data only** in demo (C1) — no real PHI, no compliance exposure
2. **Safety layer / refuse medical advice** (A2.3) — hallucination guardrails
3. **Identity verification before sensitive data** (A1.5) — caller-ID spoofing
4. **Call-recording consent greeting** (B1.10) — legal
5. **Template-first responses for latency** (A2.1) — honest fix for CPU-only
6. **Feature freeze + scope control** (F6) — protect the 15-day timeline
7. **FreeSWITCH timebox with simulated-call fallback** (A1.1)
8. **Transactional booking + confirmation required** (A3.1 + E8) — no double-book, clear liability
9. **English-only acknowledged + roadmap** (A2.2, B3.1) — prepared pitch answer
10. **Demo recovery script** (F7, G5/G6) — defined success/failure metrics
