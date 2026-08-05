<div align="center">

# 🏥 HospiCall

### A zero-cost, self-hosted AI voice receptionist for hospitals

Call in → speak to the AI → book a doctor's appointment. **Every component runs locally on a laptop** — no cloud APIs, no monthly fees, no data leaves your machine.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-Llama%203.1%208B-000000?logo=ollama)](https://ollama.com)
[![Whisper](https://img.shields.io/badge/Whisper-large--v3-FF6F00?logo=openai&logoColor=white)](https://github.com/Systran/faster-whisper)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## ✨ Demo flow

```
📞 Caller dials in
   │
   ▼
🎙️ Whisper (STT)     → "I need a cardiologist appointment tomorrow"
   │
   ▼
🧠 HospiCall Brain    → intent: book (fast-path, <100ms) — or Llama 3.1 for the rest
   │
   ▼
📅 Booking Engine     → "Cardiology has openings tomorrow at 9 AM, 10 AM, 11 AM"
   │
   ▼
🔊 OmniVoice (TTS)    → caller hears the reply, conversation continues
```

## 🚀 Why HospiCall?

| Problem | HospiCall's answer |
|---|---|
| Phone lines buried in **missed calls** | AI answers 24/7, books appointments instantly |
| **Cloud voice APIs** cost $50–500+/mo | Fully local — **$0 forever** on a laptop |
| **Patient data** sent to third parties | Self-hosted. All models run on your machine |
| **Custom voice assistants** are dev-heavy | ~20-min setup, one command to run |

It's built for the funding pitch: plug in a number, forward it, and a hospital gets a full voice receptionist that never sleeps.

## 🧠 How the brain stays fast

Every patient turn hits the **fast-path first** — a keyword-based intent + slot extractor that answers common requests instantly, no LLM round-trip:

| Turn | Path | Latency |
|---|---|---|
| "Book a cardiologist tomorrow" | Fast-path → real DB availability | **~80ms** |
| "What are your timings?" | Fast-path → template | **~80ms** |
| "Emergency!" | Fast-path → triage script | **~80ms** |
| Anything unusual (20% of turns) | Llama 3.1 8B fallback | ~15–19s (CPU) |

**Why a fallback at all?** The fast-path handles the 80% routine; the LLM handles the long tail. That split keeps real calls snappy without spending on GPU clouds.

## 📦 Tech stack

| Layer | Tool |
|---|---|
| Speech-to-Text | [Whisper large-v3](https://github.com/Systran/faster-whisper) (faster-whisper) |
| Text-to-Speech | [OmniVoice Studio](https://omnivoice.dev) (600+ languages, zero-shot voice cloning) |
| Brain | [Ollama](https://ollama.com) + Llama 3.1 8B, with keyword fast-path |
| Backend | [FastAPI](https://fastapi.tiangolo.com) + SQLite |
| Telephony (planned) | FreeSWITCH for real phone calls |

## 🛠️ Quick start

### Prerequisites
- **Python 3.12+**
- [Ollama](https://ollama.com/download) running
- [OmniVoice Studio](https://omnivoice.dev) (free, local)

### 1. Install the AI models
```bash
# Pull the brain model
ollama pull llama3.1:8b-instruct-q4_K_M
```
In **OmniVoice Studio → Settings**, install:
- `OmniVoice TTS` (TTS engine)
- `Whisper large-v3 (faster-whisper)` (ASR engine)

Then **Start Server** — confirm `http://localhost:3900`.

### 2. Run the backend
```bash
cd backend
pip install -r requirements.txt

python scripts/seed_db.py          # seed demo doctors/patient (optional)
python -m uvicorn app.main:app --port 8000
```

### 3. Make a test call
```bash
# Start a call
curl -X POST http://127.0.0.1:8000/calls/start \
     -H "Content-Type: application/json" \
     -d '{"call_id":"demo-1","phone":"9441321662"}'
# → {"greeting":"Welcome to City Hospital. How can I help you?"}

# Speak
curl -X POST http://127.0.0.1:8000/calls/message \
     -H "Content-Type: application/json" \
     -d '{"call_id":"demo-1","text":"I want a cardiology appointment tomorrow"}'
# → {"intent":"book","response":"Sure. Our cardiology department has openings tomorrow at 9 AM, 10 AM, 11 AM. Which one works best for you?"}

# Read the transcript
curl http://127.0.0.1:8000/calls/demo-1/transcript
```

## 🔌 API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/calls/start` | Start a call, returns greeting |
| `POST` | `/calls/message` | Send patient text, get `intent` + `response` |
| `POST` | `/calls/transcribe` | STT an audio file (path) |
| `GET` | `/calls/{call_id}/transcript` | Full conversation transcript |
| `POST` | `/calls/{call_id}/end` | End a call |

## 🗂️ Project structure

```
HospiCall/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI routes
│   │   ├── config.py            # settings
│   │   ├── db.py                # SQLite models
│   │   └── services/
│   │       ├── brain.py         # fast-path + Ollama wrapper
│   │       ├── voice.py         # OmniVoice STT/TTS client
│   │       └── session.py       # per-call state
│   └── scripts/
│       ├── seed_db.py           # demo doctors/patient
│       └── test_pipeline.py     # live pipeline smoke test
├── docs/                        # architecture, diagrams, assessment
├── LICENSE
└── README.md
```

## 🗺️ Roadmap

- [x] STT → intent → response → TTS pipeline end-to-end
- [x] Fast-path latency fix (<100ms for common intents)
- [x] DB-backed appointment slot suggestions
- [ ] Booking confirmation (writes appointments to DB)
- [ ] Browser demo (mic → STT → LLM → TTS → playback)
- [ ] FreeSWITCH phone integration
- [ ] PostgreSQL migration for production

## ⚠️ Privacy & ethics

> Demo uses **synthetic data only** — no real patient information is stored or processed. For production use, comply with HIPAA/GDPR and your local healthcare regulations before deployment.

## 📄 License

[MIT](LICENSE) © 2026 [Mamidala Som Praneeth Babu](https://github.com/praann07)

---

<p align="center">
  Made with ☕ & 🦙 for the demo — <b>HospiCall answers when your staff can't.</b>
</p>
