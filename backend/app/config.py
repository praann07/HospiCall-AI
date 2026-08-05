from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "HospiCall"
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b-instruct-q4_K_M"
    omnivoice_url: str = "http://localhost:3900"
    tts_engine: str = "omnivoice"
    stt_engine: str = "whisperx"
    db_path: str = "../data/hospicall.db"
    session_ttl_seconds: int = 900
    rate_limit_sms_per_day: int = 3


settings = Settings()
