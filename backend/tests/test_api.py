"""HTTP contract: validation, security caps, admin auth, rate limiting, voice endpoints with a fake speech service."""
import warnings
from datetime import datetime

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.services.ratelimit import RateLimiter
from app.services.voice import VoiceUnavailable

PHONE = "9876543210"
KEY = {"X-API-Key": "s3cret"}
NOW = datetime(2026, 11, 23, 8, 0)


class FakeVoice:
    def __init__(self, heard="book a neurologist tomorrow at 10 am", fail=False):
        self.heard, self.fail, self.transcribed_paths, self.synth_paths = heard, fail, [], []

    def transcribe(self, audio_path):
        self.transcribed_paths.append(audio_path)
        if self.fail:
            raise VoiceUnavailable("down")
        return self.heard

    def synthesize(self, text, voice, out_path):
        self.synth_paths.append(out_path)
        if self.fail:
            raise VoiceUnavailable("down")
        with open(out_path, "wb") as f:
            f.write(b"RIFF-fake-wav")
        return out_path

    def is_up(self):
        return not self.fail


def make_client(tmp_path, voice=None, **overrides):
    cfg = Settings(_env_file=None, db_path=str(tmp_path / "api.db"), rate_limit_per_minute=0,
                   admin_api_key="s3cret", **overrides)
    return TestClient(create_app(cfg, llm=None, voice=voice, clock=lambda: NOW)), cfg


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)[0]


def start(client, phone=PHONE):
    resp = client.post("/v1/calls/start", json={"phone": phone})
    assert resp.status_code == 200, resp.text
    return resp.json()["call_id"]


def say(client, call_id, text):
    resp = client.post("/v1/calls/message", json={"call_id": call_id, "text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------------------------- basics
def test_health_and_version_prefix(client):
    assert client.get("/v1/health").json() == {"status": "ok", "version": "1.0.0"}
    assert client.get("/health").status_code == 200  # legacy alias


def test_status_reports_dependencies(client):
    body = client.get("/v1/status").json()
    assert body["doctors"] == 11 and body["llm"]["configured"] is False and body["voice"]["configured"] is False


def test_start_returns_server_generated_id_and_consent_greeting(client):
    body = client.post("/v1/calls/start", json={"phone": "+91 98765-43210"}).json()
    assert len(body["call_id"]) == 32
    assert "automated AI assistant" in body["greeting"] and "Book an appointment" in body["options"]


@pytest.mark.parametrize("phone", ["", "abc", "123", "98765432101234567890", "'; DROP TABLE users;--"])
def test_start_rejects_bad_phone(client, phone):
    assert client.post("/v1/calls/start", json={"phone": phone}).status_code == 422


def test_full_booking_over_http(client):
    call_id = start(client)
    assert say(client, call_id, "book a neurologist tomorrow at 10 am")["intent"] == "book"
    say(client, call_id, "skip")
    done = say(client, call_id, "yes")
    assert done["outcome"] == "booked" and done["path"] == "rules" and done["latency_ms"] >= 0
    transcript = client.get(f"/v1/calls/{call_id}/transcript", headers=KEY).json()
    assert [s["speaker"] for s in transcript["segments"]][:3] == ["agent", "caller", "agent"]
    assert client.post(f"/v1/calls/{call_id}/end").json()["ended"] is True


def test_legacy_unversioned_aliases_still_work(client):
    call_id = client.post("/calls/start", json={"phone": PHONE}).json()["call_id"]
    assert client.post("/calls/message", json={"call_id": call_id, "text": "hello"}).status_code == 200


# ---------------------------------------------------------------------------------------------- validation
def test_unknown_call_is_404(client):
    resp = client.post("/v1/calls/message", json={"call_id": "ffffffff", "text": "hello"})
    assert resp.status_code == 404 and resp.json()["detail"] == "unknown_or_expired_call"
    assert client.get("/v1/calls/ffffffff/transcript", headers=KEY).status_code == 404
    assert client.post("/v1/calls/ffffffff/end").status_code == 404


@pytest.mark.parametrize("call_id", ["../../etc/passwd", "a b", "x" * 65, "id;rm -rf", ""])
def test_call_id_is_validated_everywhere(client, call_id):
    assert client.post("/v1/calls/message", json={"call_id": call_id, "text": "hi"}).status_code == 422
    assert client.post("/v1/calls/audio", params={"call_id": call_id},
                       files={"file": ("a.wav", b"x", "audio/wav")}).status_code in (422, 503)


def test_empty_and_oversized_text_rejected(tmp_path):
    client, cfg = make_client(tmp_path, max_text_chars=50)
    call_id = start(client)
    assert client.post("/v1/calls/message", json={"call_id": call_id, "text": "   "}).status_code == 422
    assert client.post("/v1/calls/message", json={"call_id": call_id, "text": "x" * 51}).status_code == 413
    assert client.post("/v1/calls/message", json={"call_id": call_id, "text": "x" * 6000}).status_code == 422


def test_security_headers(client):
    resp = client.post("/v1/calls/start", json={"phone": PHONE})
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "no-referrer"


# ---------------------------------------------------------------------------------------------- admin
def test_admin_disabled_without_key(tmp_path):
    cfg = Settings(_env_file=None, db_path=str(tmp_path / "x.db"), rate_limit_per_minute=0, admin_api_key=None)
    client = TestClient(create_app(cfg, llm=None, voice=None, clock=lambda: NOW))
    assert client.get("/v1/admin/audit").status_code == 403


def test_admin_requires_correct_key(client):
    assert client.get("/v1/admin/appointments").status_code == 401
    assert client.get("/v1/admin/appointments", headers={"X-API-Key": "wrong"}).status_code == 401


def test_admin_lists_masked_appointments(client):
    call_id = start(client)
    for text in ("book a neurologist tomorrow at 10 am", "skip", "yes"):
        say(client, call_id, text)
    rows = client.get("/v1/admin/appointments", headers={"X-API-Key": "s3cret"}).json()
    mine = [r for r in rows if r["doctor"] == "Dr. Iyer"]
    assert mine and mine[0]["phone"] == "******3210" and PHONE not in str(rows)


def test_admin_audit_records_sensitive_actions_with_masked_data(client):
    call_id = start(client)
    say(client, call_id, "this is an emergency")
    rows = client.get("/v1/admin/audit", headers={"X-API-Key": "s3cret"}).json()
    assert any(r["action"] == "emergency_escalation" and r["call_id"] == call_id for r in rows)
    assert PHONE not in str(rows)
    assert client.get("/v1/admin/audit", params={"limit": 0}, headers={"X-API-Key": "s3cret"}).status_code == 422


def test_admin_routes_not_in_unversioned_aliases(client):
    assert client.get("/admin/audit", headers={"X-API-Key": "s3cret"}).status_code == 404


# ---------------------------------------------------------------------------------------------- rate limit
def test_rate_limit_returns_429_but_never_blocks_health(tmp_path):
    cfg = Settings(_env_file=None, db_path=str(tmp_path / "r.db"), rate_limit_per_minute=3)
    client = TestClient(create_app(cfg, llm=None, voice=None, clock=lambda: NOW,
                                   rate_limiter=RateLimiter(3, clock=lambda: 0.0)))
    codes = [client.post("/v1/calls/start", json={"phone": PHONE}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert client.get("/v1/health").status_code == 200
    limited = client.post("/v1/calls/start", json={"phone": PHONE})
    assert limited.headers["retry-after"] == "60" and limited.json() == {"detail": "rate_limited"}


# ---------------------------------------------------------------------------------------------- voice
def audio(client, call_id, data=b"fake-audio", ctype="audio/webm"):
    return client.post("/v1/calls/audio", params={"call_id": call_id}, files={"file": ("clip.webm", data, ctype)})


def test_audio_503_when_no_speech_service(client):
    call_id = start(client)
    assert audio(client, call_id).status_code == 503


def test_audio_turn_transcribes_and_cleans_up_temp_file(tmp_path):
    fake = FakeVoice()
    client, cfg = make_client(tmp_path, voice=fake)
    call_id = start(client)
    resp = audio(client, call_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["heard"] == fake.heard and body["intent"] == "book"
    [path] = fake.transcribed_paths
    assert path.endswith(".webm") and call_id not in path  # server-chosen name, never user input
    assert not list(cfg.tmp_dir.glob("*"))  # temp audio removed (privacy)


def test_audio_temp_file_removed_even_when_stt_fails(tmp_path):
    client, cfg = make_client(tmp_path, voice=FakeVoice(fail=True))
    call_id = start(client)
    assert audio(client, call_id).status_code == 503
    assert not list(cfg.tmp_dir.glob("*"))


def test_audio_retention_flag_keeps_files(tmp_path):
    client, cfg = make_client(tmp_path, voice=FakeVoice(), audio_retention=True)
    audio(client, start(client))
    assert len(list(cfg.tmp_dir.glob("*"))) == 1


def test_audio_rejects_wrong_type_size_empty_and_unknown_call(tmp_path):
    client, _ = make_client(tmp_path, voice=FakeVoice(), max_audio_bytes=100)
    call_id = start(client)
    assert audio(client, call_id, ctype="text/html").status_code == 415
    assert audio(client, call_id, data=b"x" * 101).status_code == 413
    assert audio(client, call_id, data=b"").status_code == 422
    assert audio(client, "0" * 32).status_code == 404


def test_silence_returns_friendly_reprompt_without_agent_turn(tmp_path):
    client, _ = make_client(tmp_path, voice=FakeVoice(heard=""))
    call_id = start(client)
    body = audio(client, call_id).json()
    assert "didn't catch" in body["reply"] and body["heard"] == ""
    assert len(client.get(f"/v1/calls/{call_id}/transcript", headers=KEY).json()["segments"]) == 1  # greeting only


def test_synthesize_streams_wav_then_deletes_it(tmp_path):
    fake = FakeVoice()
    client, cfg = make_client(tmp_path, voice=fake)
    resp = client.post("/v1/synthesize", json={"text": "Hello"})
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"RIFF-fake-wav"
    assert not list(cfg.tmp_dir.glob("*"))


def test_synthesize_503_when_tts_down_or_missing(tmp_path):
    client, cfg = make_client(tmp_path, voice=FakeVoice(fail=True))
    assert client.post("/v1/synthesize", json={"text": "Hello"}).status_code == 503
    assert not list(cfg.tmp_dir.glob("*"))
    assert make_client(tmp_path / "other")[0].post("/v1/synthesize", json={"text": "Hello"}).status_code == 503
    assert client.post("/v1/synthesize", json={"text": ""}).status_code == 422


# ---------------------------------------------------------------------------------------------- ops
def test_metrics_endpoints(client):
    call_id = start(client)
    say(client, call_id, "what are your hours")
    text = client.get("/metrics").text
    assert 'hospicall_turns_total{intent="inquiry",path="rules"} 1' in text
    stats = client.get("/v1/stats").json()
    assert stats["latency"]["rules"]["count"] == 1
    assert client.get("/v1/status").json()["active_calls"] == 1


def test_static_ui_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "HospiCall" in resp.text


def test_openapi_documents_v1_only(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/calls/start" in paths and "/calls/start" not in paths
