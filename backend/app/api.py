"""HTTP surface of HospiCall. `create_app` wires every dependency explicitly so tests can inject fakes."""
import ipaddress
import logging
import re
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.background import BackgroundTask

from .bodylimit import BodyLimit
from .config import Settings
from .config import settings as default_settings
from .db import BOOKED, Appointment, Doctor, init_db
from .seed import seed_demo, seed_if_empty
from .services.agent import Agent
from .services.audit import Audit, mask_phone
from .services.calls import CallService, UnknownCall
from .services.llm import LLMClient
from .services.metrics import Metrics
from .services.ratelimit import RateLimiter
from .services.scheduler import Scheduler
from .services.session import SessionManager
from .services.voice import Voice, VoiceUnavailable

log = logging.getLogger("hospicall")

STATIC_DIR = Path(__file__).parent / "static"
CALL_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_PHONE_JUNK = re.compile(r"[\s\-().]")
_PHONE_OK = re.compile(r"^\+?[0-9]{7,15}$")  # ASCII digits only: \d also matches Arabic-Indic ones
_AUDIO_SUFFIX = {"audio/webm": ".webm", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav",
                 "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
                 "audio/flac": ".flac"}
_DEFAULT = object()  # "build the real one from settings" (None means "none at all")
PROBE_TTL_SECONDS = 5.0  # /status re-checks the LLM and speech service at most this often
# The demo UI is one inline page that talks only to this origin. /docs is exempt: Swagger UI loads CDN assets.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
_DOCS_PATHS = ("/docs", "/openapi.json")


class StartRequest(BaseModel):
    phone: str = Field(description="Caller's phone number; its last four digits are the verification code.")


class MessageRequest(BaseModel):
    call_id: str = Field(pattern=CALL_ID_PATTERN)
    text: str = Field(max_length=5000)


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    voice: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class StartResponse(BaseModel):
    call_id: str
    greeting: str
    options: list[str]


class TurnResponse(BaseModel):
    call_id: str
    reply: str
    intent: str
    path: str
    options: list[str]
    end_call: bool
    outcome: str | None
    latency_ms: float
    heard: str | None = None


def _normalise_phone(raw: str) -> str:
    phone = _PHONE_JUNK.sub("", raw)
    if not _PHONE_OK.match(phone):
        raise HTTPException(422, "phone must be 7-15 digits, optionally starting with +")
    return phone


def _client_key(request: Request) -> str:
    """Rate-limit identity: the socket peer (X-Forwarded-For is client-controlled, so it is never trusted).
    An IPv6 client owns a whole /64, so it is keyed by network, not by address."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        return str(ip.ipv4_mapped) if ip.ipv4_mapped else str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return host


def create_app(settings: Settings | None = None, *, llm=_DEFAULT, voice=_DEFAULT, clock=None,
               rate_limiter: RateLimiter | None = None) -> FastAPI:
    cfg = settings or default_settings
    clock = clock or datetime.now
    session_factory = init_db(cfg.db_path)
    if cfg.auto_seed:
        seed_if_empty(session_factory)
        seed_demo(session_factory, clock())
    llm = (LLMClient(cfg.ollama_url, cfg.llm_model, cfg.hospital_name, cfg.llm_timeout_seconds)
           if llm is _DEFAULT else llm)
    voice = (Voice(cfg.omnivoice_url, cfg.tts_engine, cfg.stt_engine, cfg.voice_timeout_seconds)
             if voice is _DEFAULT else voice)
    audit, metrics = Audit(session_factory), Metrics()
    scheduler = Scheduler(session_factory, clock)
    agent = Agent(scheduler, cfg, llm, audit)
    calls = CallService(agent, SessionManager(cfg.session_ttl_seconds, clock), session_factory, metrics)
    limiter = rate_limiter or RateLimiter(cfg.rate_limit_per_minute)
    # Stricter per-route budgets on top of the global one; rate_limit_per_minute=0 switches all of them off.
    on = cfg.rate_limit_per_minute > 0
    start_limiter = RateLimiter(cfg.rate_limit_start_per_minute if on else 0)
    voice_limiter = RateLimiter(cfg.rate_limit_voice_per_minute if on else 0)
    route_limiters = {"/calls/start": start_limiter, "/calls/audio": voice_limiter, "/synthesize": voice_limiter}
    probe = {"at": float("-inf"), "llm": False, "voice": False}

    app = FastAPI(title=cfg.app_name, version=cfg.version, docs_url="/docs", redoc_url=None,
                  description="Voice and text AI receptionist for hospital appointments.")
    app.state.calls, app.state.scheduler, app.state.metrics, app.state.audit = calls, scheduler, metrics, audit

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path not in ("/health", "/v1/health") and not path.startswith("/static"):
            client = _client_key(request)
            route = route_limiters.get(path.removeprefix("/v1"))
            if not limiter.allow(client) or (route is not None and not route.allow(client)):
                metrics.inc("hospicall_rate_limited_total")
                return JSONResponse({"detail": "rate_limited"}, status_code=429, headers={"Retry-After": "60"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        if path not in _DOCS_PATHS:
            response.headers["Content-Security-Policy"] = CSP
            response.headers["X-Frame-Options"] = "DENY"
        if path.startswith(("/v1", "/calls", "/synthesize")):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Outermost: an oversized body is refused before anything reads it.
    app.add_middleware(BodyLimit, max_json_bytes=cfg.max_json_bytes, max_audio_bytes=cfg.max_audio_bytes)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default echoes the submitted input back; we return only where and why it was rejected.
        detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    public, admin = APIRouter(tags=["calls"]), APIRouter(prefix="/admin", tags=["admin"])

    def turn_response(call_id: str, text: str, heard: str | None = None) -> TurnResponse:
        try:
            result = calls.turn(call_id, text)
        except UnknownCall:
            raise HTTPException(404, "unknown_or_expired_call") from None
        r = result.reply
        log.info("turn call=%s intent=%s path=%s latency_ms=%s", call_id, r.intent, r.path, result.latency_ms)
        return TurnResponse(call_id=call_id, reply=r.text, intent=r.intent, path=r.path, options=r.options,
                            end_call=r.end_call, outcome=r.outcome, latency_ms=result.latency_ms, heard=heard)

    @public.get("/health", summary="Liveness probe")
    def health() -> dict:
        return {"status": "ok", "version": cfg.version}

    @public.get("/status", summary="Dependency status (database, LLM, speech service)")
    def status() -> dict:
        with session_factory() as db:
            doctors = len(db.scalars(select(Doctor.id)).all())
        now = time.monotonic()
        if now - probe["at"] > PROBE_TTL_SECONDS:  # unauthenticated + network calls: never probe once per request
            probe.update(at=now, llm=bool(llm and llm.is_up()), voice=bool(voice and voice.is_up()))
        return {"status": "ok", "version": cfg.version, "doctors": doctors, "active_calls": calls.sessions.active(),
                "llm": {"configured": llm is not None, "up": probe["llm"], "model": cfg.llm_model},
                "voice": {"configured": voice is not None, "up": probe["voice"]}}

    @public.post("/calls/start", response_model=StartResponse, summary="Start a call")
    def start_call(body: StartRequest) -> StartResponse:
        session, greeting = calls.start(_normalise_phone(body.phone))
        return StartResponse(call_id=session.call_id, greeting=greeting, options=["Book an appointment",
                             "Cancel appointment", "Hospital hours", "Our doctors"])

    @public.post("/calls/message", response_model=TurnResponse, summary="Send one caller utterance (text)")
    def message(body: MessageRequest) -> TurnResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "text must not be empty")
        if len(text) > cfg.max_text_chars:
            raise HTTPException(413, f"text exceeds {cfg.max_text_chars} characters")
        return turn_response(body.call_id, text)

    @public.post("/calls/audio", response_model=TurnResponse, summary="Send one caller utterance (audio)")
    def audio(file: UploadFile = File(...), call_id: str = Query(pattern=CALL_ID_PATTERN)) -> TurnResponse:
        if voice is None:
            raise HTTPException(503, "voice_unavailable")
        ctype = (file.content_type or "").split(";")[0].strip().lower()
        if not (ctype.startswith(("audio/", "video/webm")) or ctype == "application/octet-stream"):
            raise HTTPException(415, "upload must be an audio file")
        if calls.sessions.get(call_id) is None:  # before reading: a stranger's upload is not worth the memory
            raise HTTPException(404, "unknown_or_expired_call")
        data = file.file.read(cfg.max_audio_bytes + 1)
        if len(data) > cfg.max_audio_bytes:
            raise HTTPException(413, f"audio exceeds {cfg.max_audio_bytes} bytes")
        if not data:
            raise HTTPException(422, "empty audio upload")
        cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.tmp_dir / f"{uuid.uuid4().hex}{_AUDIO_SUFFIX.get(ctype, '.bin')}"
        path.write_bytes(data)
        try:
            heard = voice.transcribe(str(path))
        except VoiceUnavailable as exc:
            raise HTTPException(503, "voice_unavailable") from exc
        finally:
            if not cfg.audio_retention:
                path.unlink(missing_ok=True)
        if not heard:
            return TurnResponse(call_id=call_id, reply="Sorry, I didn't catch that. Could you say it again?",
                                intent="unclear", path="fallback", options=[], end_call=False, outcome=None,
                                latency_ms=0.0, heard="")
        return turn_response(call_id, heard[:cfg.max_text_chars], heard=heard)

    @public.post("/synthesize", summary="Text to speech (WAV)")
    def synthesize(body: SynthesizeRequest):
        if voice is None:
            raise HTTPException(503, "voice_unavailable")
        cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.tmp_dir / f"{uuid.uuid4().hex}.wav"
        try:
            voice.synthesize(body.text, body.voice or cfg.tts_voice, str(path))
        except VoiceUnavailable as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(503, "voice_unavailable") from exc
        return FileResponse(path, media_type="audio/wav", background=BackgroundTask(path.unlink, missing_ok=True))

    @public.post("/calls/{call_id}/end", summary="End a call")
    def end_call(call_id: str = PathParam(pattern=CALL_ID_PATTERN)) -> dict:
        if calls.transcript(call_id) is None:
            raise HTTPException(404, "unknown_call")
        return {"call_id": call_id, "ended": True, "was_active": calls.end(call_id)}

    def require_admin(key: str | None) -> None:
        if not cfg.admin_api_key:
            raise HTTPException(403, "admin_api_disabled")
        if not key or not secrets.compare_digest(key.encode(), cfg.admin_api_key.encode()):
            raise HTTPException(401, "invalid_api_key")

    @public.get("/calls/{call_id}/transcript", summary="Saved transcript (admin key; empty if the caller opted out)")
    def transcript(call_id: str = PathParam(pattern=CALL_ID_PATTERN),
                   x_api_key: str | None = Header(default=None)) -> dict:
        require_admin(x_api_key)
        segments = calls.transcript(call_id)
        if segments is None:
            raise HTTPException(404, "unknown_call")
        return {"call_id": call_id, "segments": segments}

    @public.get("/stats", summary="Counters and latency percentiles as JSON")
    def stats() -> dict:
        return metrics.snapshot()

    @admin.get("/appointments", summary="Upcoming booked appointments (phone numbers masked)")
    def admin_appointments(x_api_key: str | None = Header(default=None),
                           limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        require_admin(x_api_key)
        stmt = (select(Appointment, Doctor).join(Doctor, Doctor.id == Appointment.doctor_id)
                .where(Appointment.status == BOOKED, Appointment.slot_start >= clock())
                .order_by(Appointment.slot_start).limit(limit))
        with session_factory() as db:
            return [{"id": a.id, "doctor": d.name, "specialization": d.specialization,
                     "start": a.slot_start.isoformat(), "patient": a.patient_name,
                     "phone": mask_phone(a.user_phone)} for a, d in db.execute(stmt)]

    @admin.get("/audit", summary="Append-only audit trail (most recent first)")
    def admin_audit(x_api_key: str | None = Header(default=None), limit: int = Query(100, ge=1, le=1000),
                    call_id: str | None = Query(default=None, pattern=CALL_ID_PATTERN)) -> list[dict]:
        require_admin(x_api_key)
        return audit.recent(limit, call_id)

    app.include_router(public, prefix="/v1")
    app.include_router(admin, prefix="/v1")
    app.include_router(public, include_in_schema=False)  # unversioned aliases kept for older clients

    @app.get("/metrics", response_class=PlainTextResponse, tags=["ops"], summary="Prometheus metrics")
    def prometheus() -> str:
        return metrics.prometheus()

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
