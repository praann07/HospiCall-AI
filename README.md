# HospiCall

A self-hosted, zero-cost hospital AI voice agent demo. A caller phones in, speaks to an AI receptionist, and books a doctor's appointment — all running locally on a laptop.

## Stack

- **STT / TTS**: [OmniVoice Studio](https://omnivoice.dev) (local server on `localhost:3900`, OpenAI-compatible REST API)
- **Brain**: Llama 3.1 8B via [Ollama](https://ollama.com), with a keyword fast-path for instant replies to common intents
- **Backend**: FastAPI + SQLite
- **Telephony**: FreeSWITCH (planned) for real phone calls

## Quick start

```powershell
# 1. Start OmniVoice Studio (Settings → Start Server), confirm localhost:3900
# 2. Start Ollama and pull the model
ollama pull llama3.1:8b-instruct-q4_K_M

# 3. Install backend deps and run
cd backend
pip install -r requirements.txt
python scripts/seed_db.py      # seed doctors/patient (optional)
python -m uvicorn app.main:app --port 8000
```

## API

| Endpoint | Description |
|---|---|
| `POST /calls/start` | Start a call, get greeting |
| `POST /calls/message` | Send patient text, get AI reply (`intent` + `response`) |
| `POST /calls/transcribe` | STT an audio file |
| `GET /calls/{call_id}/transcript` | Full conversation transcript |
| `POST /calls/{call_id}/end` | End a call |

## Latency design

Every patient turn runs through a fast-path first:

1. **Keyword intent + slot extraction** — instant (~80ms), no LLM
2. **DB-backed template response** — booking offers real available slots
3. **LLM fallback** (Llama 3.1 8B) — only for the ~20% of turns the fast path can't answer (~15-19s on CPU)

## Project structure

```
backend/
  app/
    main.py              # FastAPI routes
    config.py            # settings
    db.py                # SQLite models
    services/
      brain.py           # fast-path + Ollama wrapper
      voice.py           # OmniVoice STT/TTS HTTP client
      session.py         # per-call state
  scripts/
    seed_db.py           # demo data
    test_pipeline.py     # live pipeline smoke test
docs/                    # design docs (architecture, diagrams, assessment)
```

## Status

Voice pipeline working end-to-end on a CPU-only laptop: STT → intent → response (fast-path <100ms) → TTS. Real phone integration via FreeSWITCH is the remaining milestone.

> Demo uses synthetic data only. No real patient information.
