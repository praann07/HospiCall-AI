# AI Voice Agent for Dental Clinics — Zero-Cost Demo Plan

## Goal
Build a production-grade AI voice agent demo for dental clinics (and adaptable to other service businesses) using zero cost. Use personal phone number (9441321662) for the demo. Show to recruiters/college/hospitals for funding.

---

## Architecture Overview

Patient calls 9441321662 → call forwarding → FreeSWITCH (SIP) → AI pipeline → patient hears AI response

### Pipeline
1. FreeSWITCH receives the call (PSTN → VoIP bridge)
2. Whisper (STT) converts patient speech to text
3. Ollama + Llama 3 (NLU) understands intent and generates response text
4. OmniVoice-Studio REST API (TTS) converts response text to speech
5. Patient hears the AI voice

---

## Zero-Cost Stack

| Layer | Tool | Source | Cost |
|-------|------|--------|------|
| Phone → SIP | FreeSWITCH | Free, self-hosted | $0 |
| STT | OmniVoice-Studio (WhisperX) | GitHub | $0 |
| NLU + Response | Ollama + Llama 3 8B | ollama.ai | $0 |
| TTS | OmniVoice-Studio (Piper/Chatterbox) | GitHub | $0 |
| Database | SQLite | Built-in | $0 |
| Server | Your laptop | Own hardware | $0 |
| Phone number | 9441321662 (call forwarding) | Existing | $0 |

**Total cost: $0**

---

## Why OmniVoice-Studio (Not Voicebox)

- 14 TTS engines vs Voicebox's 7
- 11 ASR engines vs Voicebox's 2
- OpenAI-compatible REST API at localhost:3900
- Voice cloning, multilingual (646 languages)
- Runs fully local, no cloud, no subscriptions
- AGPL-3.0 license, free and open-source

### Repo
- https://github.com/debpalash/OmniVoice-Studio

### API Usage (not MCP)
Use the REST API for the custom pipeline:
```
POST http://localhost:3900/v1/audio/speech
{
  "model": "tts-1",
  "voice": "cloned-voice-id",
  "input": "Your appointment is confirmed for Tuesday at 3 PM.",
  "response_format": "wav"
}
```

MCP is for AI agents (Claude, Cursor) to use OmniVoice as a tool — not for building a custom voice agent pipeline.

---

## Phone Number Setup

- Use existing number: 9441321662
- Set up call forwarding with your telecom provider (Jio/Airtel)
- Forward to FreeSWITCH SIP endpoint on your laptop
- If provider doesn't support direct SIP forwarding, use a free SIP service as intermediate

---

## Concurrent Call Handling

- Each incoming call creates an isolated session
- FreeSWITCH handles multiple calls in parallel
- Each session runs STT → NLU → TTS independently
- The AI model is shared across sessions (loaded once in memory)
- Conversation state is isolated per session

---

## Demo Scope

- Single phone number (9441321662)
- Single server (your laptop)
- Zero cost
- Demonstrates: voice cloning, STT, intent understanding, TTS, phone integration
- Not production-scale — this is a proof-of-concept for funding

---

## Scaling Path (When Funded)

1. Move from laptop to cloud server (VPS/VM)
2. Add GPU for faster inference
3. Multiple virtual numbers for load distribution
4. Proper database (PostgreSQL) instead of SQLite
5. Multi-tenant architecture for multiple clinics
6. Monitoring, logging, failure handling

---

## Key Decisions

- No paid APIs (ElevenLabs, n8n, etc.) — everything self-hosted
- No code in this document — architecture and decisions only
- Voicebox.sh was considered but OmniVoice-Studio is stronger (more engines, better API)
- MCP not used — REST API is the right integration method for custom pipelines
- FreeSWITCH for telephony — open-source, handles PSTN ↔ VoIP bridging