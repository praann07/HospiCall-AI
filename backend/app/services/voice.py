import httpx


class Voice:
    """STT + TTS wrapper around OmniVoice-Studio's OpenAI-compatible API."""

    def __init__(self, base_url: str, tts_engine: str, stt_engine: str):
        self.base_url = base_url.rstrip("/")
        self.tts_engine = tts_engine
        self.stt_engine = stt_engine

    def transcribe(self, audio_path: str) -> str:
        """Speech-to-text: audio file -> text."""
        url = f"{self.base_url}/v1/audio/transcriptions"
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path, f, "audio/wav")}
            data = {"model": self.stt_engine, "response_format": "json"}
            with httpx.Client(timeout=120) as client:
                resp = client.post(url, files=files, data=data)
                resp.raise_for_status()
                return resp.json()["text"].strip()

    def synthesize(self, text: str, voice: str, out_path: str) -> str:
        """Text-to-speech: text -> wav file."""
        url = f"{self.base_url}/v1/audio/speech"
        payload = {
            "model": self.tts_engine,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(resp.content)
        return out_path
