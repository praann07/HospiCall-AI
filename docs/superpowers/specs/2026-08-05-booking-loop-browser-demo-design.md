# HospiCall — Booking Loop & Browser Demo (Design)

Date: 2026-08-05

## Goal
1. Make the AI actually **book appointments** in the DB (confirm-then-book with conflict safety).
2. Add a **browser demo** with real voice: mic → STT → brain → TTS → playback + live transcript.

## Phase 1 — Booking loop

### Intent flow
- Fast-path `book` → AI offers slots and stores a **pending booking** in the session: `{specialty, timing, offered: ["9 AM","10 AM","11 AM"]}`.
- Caller picks a time → new intent `confirm_booking` detected by keyword rule (matches `9 AM` / `10 AM` / `2 PM` / `4 PM` / `noon` etc., optionally with "book/yes/that works").
- On confirm:
  1. Re-check the exact slot is still free (conflict check right before insert).
  2. Insert `Appointment(user_phone, doctor_id, slot_start, slot_end)`.
  3. Reply: *"Booked, Dr. Mehta, tomorrow at 10 AM. Anything else?"*
  4. Clear pending booking.
- If slot taken meanwhile → offer nearest alternative from a fresh `find_slots` call.

### Conflict safety
- `find_slots` already filters booked slots.
- Confirmation re-queries the slot and only inserts if free (single-check-then-insert; SQLite serializes writes).
- Two callers cannot double-book the same slot.

## Phase 2 — Browser demo

- FastAPI serves static `index.html` at `/` (SingleFileApp, no build step).
- New endpoint `POST /calls/audio`:
  - Accepts multipart audio upload (`UploadFile`).
  - Reuses a shared `process_text(call_id, text) -> {intent, response}` helper (same logic as `/calls/message`).
  - Returns `{call_id, intent, user_text, ai_text}`.
- New endpoint `GET /calls/{call_id}/reply/{turn}.wav` or TTS inline: simplest is `POST /calls/audio` returns AI text; separate `POST /synthesize` returns WAV bytes so browser plays it.
- Page: press-and-hold mic button → record via MediaRecorder → POST audio → STT → brain → show live transcript → auto-play TTS reply.

### Shared helper
- `process_text(call_id, text)` centralizes fast-path + LLM + transcript persistence. Used by both `/calls/message` and `/calls/audio`.

## Files touched
- `backend/app/services/brain.py` — add `confirm_booking` keyword detection + pending-booking slot parse.
- `backend/app/services/session.py` — support `pending_booking` in session dict.
- `backend/app/main.py` — `process_text` helper, `/calls/audio`, `/synthesize`, confirm-booking path, static serving.
- `backend/static/index.html` — new browser demo.
- `backend/scripts/test_pipeline.py` — extend to cover booking confirmation.
