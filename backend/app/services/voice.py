"""Speech-to-text and text-to-speech through OmniVoice Studio's OpenAI-compatible API."""
import os

import httpx

from .resilience import BreakerOpen, CircuitBreaker

_MIME = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
         ".m4a": "audio/mp4", ".flac": "audio/flac"}


class VoiceUnavailable(Exception):
    """The speech service is down, slow, or returned something unusable."""


class Voice:
    def __init__(self, base_url: str, tts_engine: str, stt_engine: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None, breaker: CircuitBreaker | None = None):
        self.base_url = base_url.rstrip("/")
        self.tts_engine = tts_engine
        self.stt_engine = stt_engine
        self.timeout = timeout
        self._transport = transport
        self.breaker = breaker or CircuitBreaker()

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(timeout=timeout or self.timeout, transport=self._transport)

    def _post(self, path: str, **kwargs) -> httpx.Response:
        def go() -> httpx.Response:
            with self._client() as client:
                resp = client.post(f"{self.base_url}{path}", **kwargs)
                resp.raise_for_status()
                return resp
        try:
            return self.breaker.call(go)
        except (httpx.HTTPError, BreakerOpen) as exc:
            raise VoiceUnavailable(str(exc) or exc.__class__.__name__) from exc

    def transcribe(self, audio_path: str) -> str:
        """Audio file -> text. Raises VoiceUnavailable if the STT engine cannot be reached."""
        name = os.path.basename(audio_path)
        mime = _MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
        with open(audio_path, "rb") as f:
            resp = self._post("/v1/audio/transcriptions", files={"file": (name, f, mime)},
                              data={"model": self.stt_engine, "response_format": "json"})
        try:
            return str(resp.json()["text"]).strip()
        except (ValueError, KeyError) as exc:
            raise VoiceUnavailable("STT returned an unexpected payload") from exc

    def synthesize(self, text: str, voice: str, out_path: str) -> str:
        """Text -> wav file. Raises VoiceUnavailable if the TTS engine cannot be reached."""
        resp = self._post("/v1/audio/speech", json={"model": self.tts_engine, "voice": voice, "input": text,
                                                    "response_format": "wav"})
        with open(out_path, "wb") as f:
            f.write(resp.content)
        return out_path

    def is_up(self) -> bool:
        """Cheap reachability probe for the status endpoint (any HTTP answer means the server is alive)."""
        try:
            with self._client(timeout=1.5) as client:
                client.get(self.base_url)
            return True
        except httpx.HTTPError:
            return False
