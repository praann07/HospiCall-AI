"""Abuse resistance of the HTTP layer: body caps, admin-only transcripts, CSP, per-route limits, safe errors."""
import warnings
from datetime import datetime
from types import SimpleNamespace

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from app.api import PROBE_TTL_SECONDS, _client_key, create_app
from app.bodylimit import BodyLimit
from app.config import Settings
from app.services.ratelimit import RateLimiter

PHONE = "9876543210"
NOW = datetime(2026, 11, 23, 8, 0)
KEY = {"X-API-Key": "s3cret"}
JSON = {"content-type": "application/json"}


class CountingLLM:
    model = "fake"

    def __init__(self):
        self.probes = 0

    def is_up(self):
        self.probes += 1
        return True


def make_client(tmp_path, llm=None, **overrides):
    overrides.setdefault("rate_limit_per_minute", 0)
    cfg = Settings(_env_file=None, db_path=str(tmp_path / "h.db"), admin_api_key="s3cret", **overrides)
    return TestClient(create_app(cfg, llm=llm, voice=None, clock=lambda: NOW)), cfg


def start(client):
    return client.post("/v1/calls/start", json={"phone": PHONE}).json()["call_id"]


# ------------------------------------------------------------------------------------------ body size caps
def test_oversized_json_body_is_refused_before_parsing(tmp_path):
    client, _ = make_client(tmp_path, max_json_bytes=1024)
    resp = client.post("/v1/calls/message", content=b"x" * 2000, headers={"content-type": "application/json"})
    assert resp.status_code == 413 and resp.json() == {"detail": "request_too_large"}


def test_chunked_upload_without_length_is_refused(tmp_path):
    client, _ = make_client(tmp_path)
    chunks = (part for part in (b'{"phone":', b'"9876543210"}'))
    resp = client.post("/v1/calls/start", content=chunks, headers={"content-type": "application/json"})
    assert resp.status_code == 411 and resp.json() == {"detail": "length_required"}


def test_audio_route_gets_the_larger_cap(tmp_path):
    client, _ = make_client(tmp_path, max_json_bytes=1024, max_audio_bytes=5000)
    call_id = start(client)
    # 4 kB of audio is far over the JSON cap yet allowed here; the route then answers 503 (no speech service).
    ok = client.post("/v1/calls/audio", params={"call_id": call_id},
                     files={"file": ("a.webm", b"a" * 4000, "audio/webm")})
    assert ok.status_code == 503
    too_big = client.post("/v1/calls/audio", params={"call_id": call_id},
                          files={"file": ("a.webm", b"a" * 80_000, "audio/webm")})
    assert too_big.status_code == 413 and too_big.json() == {"detail": "request_too_large"}


def test_non_numeric_content_length_is_a_400():
    sent = []

    async def downstream(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("request should have been refused")

    async def send(message):
        sent.append(message)

    import asyncio

    scope = {"type": "http", "method": "POST", "path": "/v1/calls/message",
             "headers": [(b"content-length", b"12abc")]}
    asyncio.run(BodyLimit(downstream, 1024, 1024)(scope, None, send))
    assert sent[0]["status"] == 400


def test_get_requests_are_never_body_limited(tmp_path):
    client, _ = make_client(tmp_path, max_json_bytes=16)
    assert client.get("/v1/health").status_code == 200


# ------------------------------------------------------------------------------------------ transcripts
def test_transcript_needs_the_admin_key(tmp_path):
    client, _ = make_client(tmp_path)
    call_id = start(client)
    assert client.get(f"/v1/calls/{call_id}/transcript").status_code == 401
    assert client.get(f"/v1/calls/{call_id}/transcript", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get(f"/v1/calls/{call_id}/transcript", headers=KEY).status_code == 200


def test_transcript_is_closed_when_no_admin_key_is_configured(tmp_path):
    cfg = Settings(_env_file=None, db_path=str(tmp_path / "n.db"), rate_limit_per_minute=0, admin_api_key=None)
    client = TestClient(create_app(cfg, llm=None, voice=None, clock=lambda: NOW))
    call_id = client.post("/v1/calls/start", json={"phone": PHONE}).json()["call_id"]
    assert client.get(f"/v1/calls/{call_id}/transcript").status_code == 403


# ------------------------------------------------------------------------------------------ input validation
def test_unicode_digits_are_not_phone_digits(tmp_path):
    client, _ = make_client(tmp_path)
    arabic_indic = "٩٨٧٦٥٤٣٢١٠"
    assert client.post("/v1/calls/start", json={"phone": arabic_indic}).status_code == 422


@pytest.mark.parametrize("voice", ["../../etc/passwd", "a b", "x" * 65, "voice;rm"])
def test_voice_name_is_restricted(tmp_path, voice):
    client, _ = make_client(tmp_path)
    assert client.post("/v1/synthesize", json={"text": "hi", "voice": voice}).status_code == 422


def test_validation_errors_do_not_echo_input_or_crash_on_lone_surrogates(tmp_path):
    client, _ = make_client(tmp_path)
    resp = client.post("/v1/calls/message", content=b'{"call_id":"\\ud800","text":"hi"}', headers=JSON)
    assert resp.status_code == 422
    [problem] = resp.json()["detail"]
    assert set(problem) == {"loc", "msg", "type"}


def test_lone_surrogate_in_caller_text_is_a_clean_422(tmp_path):
    client, _ = make_client(tmp_path)
    call_id = start(client)
    body = b'{"call_id":"' + call_id.encode() + b'","text":"hello \\ud800 there"}'  # JSON-escaped surrogate
    resp = client.post("/v1/calls/message", content=body, headers=JSON)
    assert resp.status_code == 422 and "detail" in resp.json()  # rejected by validation, never a 500


# ------------------------------------------------------------------------------------------ headers
def test_csp_and_framing_headers_on_api_and_ui(tmp_path):
    client, _ = make_client(tmp_path)
    for path in ("/v1/health", "/"):
        headers = client.get(path).headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-frame-options"] == "DENY"
        assert "microphone=(self)" in headers["permissions-policy"]


def test_swagger_docs_keep_working_without_csp(tmp_path):
    client, _ = make_client(tmp_path)
    resp = client.get("/docs")
    assert resp.status_code == 200 and "content-security-policy" not in resp.headers


# ------------------------------------------------------------------------------------------ rate limits
def test_call_start_has_its_own_stricter_budget(tmp_path):
    client, _ = make_client(tmp_path, rate_limit_per_minute=1000, rate_limit_start_per_minute=2)
    codes = [client.post("/v1/calls/start", json={"phone": PHONE}).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]
    assert client.get("/v1/status").status_code == 200  # other routes keep the global budget


def test_legacy_alias_shares_the_route_budget(tmp_path):
    client, _ = make_client(tmp_path, rate_limit_per_minute=1000, rate_limit_start_per_minute=1)
    assert client.post("/v1/calls/start", json={"phone": PHONE}).status_code == 200
    assert client.post("/calls/start", json={"phone": PHONE}).status_code == 429


def test_zero_global_limit_switches_every_budget_off(tmp_path):
    client, _ = make_client(tmp_path, rate_limit_per_minute=0, rate_limit_start_per_minute=1)
    assert [client.post("/v1/calls/start", json={"phone": PHONE}).status_code for _ in range(3)] == [200] * 3


@pytest.mark.parametrize("host, expected", [
    ("203.0.113.9", "203.0.113.9"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:bbbb::7", "2001:db8:1:2::/64"),
    ("::ffff:198.51.100.4", "198.51.100.4"),
    ("testclient", "testclient"),
])
def test_client_key_groups_ipv6_by_network(host, expected):
    assert _client_key(SimpleNamespace(client=SimpleNamespace(host=host))) == expected


def test_client_key_without_peer():
    assert _client_key(SimpleNamespace(client=None)) == "unknown"


def test_x_forwarded_for_is_not_trusted(tmp_path):
    client, _ = make_client(tmp_path, rate_limit_per_minute=1000, rate_limit_start_per_minute=1)
    codes = [client.post("/v1/calls/start", json={"phone": PHONE}, headers={"X-Forwarded-For": ip}).status_code
             for ip in ("1.1.1.1", "2.2.2.2")]
    assert codes == [200, 429]  # a spoofed header buys no fresh budget


def test_rate_limiter_memory_is_bounded():
    now = [0.0]
    limiter = RateLimiter(5, window_seconds=60, clock=lambda: now[0], max_keys=3)
    for i in range(50):
        assert limiter.allow(f"client-{i}")
    assert len(limiter._hits) <= 3


def test_rate_limiter_prunes_idle_clients_before_evicting_active_ones():
    now = [0.0]
    limiter = RateLimiter(1, window_seconds=60, clock=lambda: now[0], max_keys=2)
    limiter.allow("idle")
    now[0] = 30.0
    limiter.allow("active")
    now[0] = 61.0  # "idle" is now stale, "active" is still inside its window
    limiter.allow("new")
    assert set(limiter._hits) == {"active", "new"}
    assert not limiter.allow("active")  # its history survived, so it is still throttled


# ------------------------------------------------------------------------------------------ /status
def test_status_probes_dependencies_at_most_once_per_ttl(tmp_path, monkeypatch):
    llm = CountingLLM()
    client, _ = make_client(tmp_path, llm=llm)
    clock = [1000.0]
    monkeypatch.setattr("app.api.time.monotonic", lambda: clock[0])
    for _ in range(5):
        assert client.get("/v1/status").json()["llm"]["up"] is True
    assert llm.probes == 1
    clock[0] += PROBE_TTL_SECONDS + 1
    client.get("/v1/status")
    assert llm.probes == 2
