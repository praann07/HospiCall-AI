"""Runtime configuration. Every value can be overridden with a ``HOSPICALL_*`` env var or ``.env``."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOSPICALL_", env_file=".env", extra="ignore")

    app_name: str = "HospiCall"
    version: str = "1.0.0"

    # Hospital facts the receptionist may state. Keep these here, never inside prompts.
    hospital_name: str = "City Hospital"
    hospital_address: str = "100 Health Avenue, opposite the city park"
    hospital_hours: str = "Monday to Saturday, 8 AM to 8 PM"
    emergency_number: str = "108"
    crisis_number: str = "14416"  # Tele-MANAS mental-health helpline (India)
    # Demo values for the fictional City Hospital; replace with your own via env vars.
    hospital_phone: str = "040 5555 0100"
    hospital_parking: str = "Free visitor parking is available in the basement and the east lot."
    hospital_fees: str = "Consultation fees depend on the doctor; the reception desk confirms the amount at check-in."
    hospital_insurance: str = (
        "We accept most major insurers and cashless claims; please bring your insurance card to reception.")

    # Local AI stack (all optional at runtime: the agent degrades gracefully when they are down).
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b-instruct-q4_K_M"
    llm_timeout_seconds: float = 20.0
    omnivoice_url: str = "http://localhost:3900"
    tts_engine: str = "kittentts"
    tts_voice: str = "en-us-libritts-high"
    stt_engine: str = "whisperx"
    voice_timeout_seconds: float = 60.0

    # Storage / sessions
    db_path: str = str(REPO_ROOT / "data" / "hospicall.db")
    auto_seed: bool = True
    session_ttl_seconds: int = 900
    audio_retention: bool = False  # temp audio is deleted right after use unless this is true

    # Abuse limits
    max_text_chars: int = 500
    max_audio_bytes: int = 10 * 1024 * 1024
    max_json_bytes: int = 64 * 1024
    rate_limit_per_minute: int = 120          # per client; 0 turns every rate limit off
    rate_limit_start_per_minute: int = 10     # POST /calls/start: each call creates a session and DB rows
    rate_limit_voice_per_minute: int = 30     # /calls/audio and /synthesize: they drive the speech service

    # Admin endpoints are disabled until a key is configured.
    admin_api_key: str | None = None

    @property
    def tmp_dir(self) -> Path:
        return Path(self.db_path).resolve().parent / "tmp"


settings = Settings()
