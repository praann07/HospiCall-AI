import json

import httpx
import pytest

from app.services.llm import LLMClient
from app.services.metrics import Metrics, percentile
from app.services.ratelimit import RateLimiter
from app.services.resilience import BreakerOpen, CircuitBreaker
from app.services.voice import Voice, VoiceUnavailable


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _boom():
    raise RuntimeError("down")


def test_breaker_opens_after_threshold_and_recovers_after_reset():
    t = FakeTime()
    breaker = CircuitBreaker(failure_threshold=2, reset_seconds=10, clock=t)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(_boom)
    assert breaker.state == "open"
    with pytest.raises(BreakerOpen):
        breaker.call(lambda: "never runs")
    t.t = 10
    assert breaker.state == "half_open"
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.state == "closed"


def test_failed_half_open_probe_reopens_the_breaker():
    t = FakeTime()
    breaker = CircuitBreaker(failure_threshold=1, reset_seconds=5, clock=t)
    with pytest.raises(RuntimeError):
        breaker.call(_boom)
    t.t = 5
    with pytest.raises(RuntimeError):
        breaker.call(_boom)
    assert breaker.state == "open"


def test_success_resets_the_failure_count():
    breaker = CircuitBreaker(failure_threshold=2)
    with pytest.raises(RuntimeError):
        breaker.call(_boom)
    breaker.call(lambda: 1)
    with pytest.raises(RuntimeError):
        breaker.call(_boom)
    assert breaker.state == "closed"


def test_rate_limiter_blocks_over_limit_and_frees_after_window():
    t = FakeTime()
    limiter = RateLimiter(2, window_seconds=60, clock=t)
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")            # keys are independent
    t.t = 61
    assert limiter.allow("a")


def test_rate_limiter_zero_disables_limiting():
    limiter = RateLimiter(0)
    assert all(limiter.allow("a") for _ in range(1000))


def test_percentile_and_metrics_snapshot():
    assert percentile([], 95) == 0.0
    assert percentile([10, 20, 30, 40], 50) in (20, 25, 30)
    m = Metrics()
    m.observe_turn("rules", "book", 12.0)
    m.observe_turn("rules", "book", 20.0)
    m.inc("hospicall_outcomes_total", outcome="booked")
    snap = m.snapshot()
    assert snap["latency"]["rules"]["count"] == 2
    text = m.prometheus()
    assert "hospicall_outcomes_total" in text and 'outcome="booked"' in text


# ---- LLM client ---------------------------------------------------------------------------------
def _llm(handler, **kw):
    return LLMClient("http://ollama.test", "m", transport=httpx.MockTransport(handler), **kw)


def _ok(content: dict):
    return lambda request: httpx.Response(200, json={"message": {"content": json.dumps(content)}})


DEPTS = ["Cardiology", "Neurology"]


def test_llm_route_returns_validated_dict():
    llm = _llm(_ok({"intent": "book", "specialty": "Cardiology", "reply": "Sure."}))
    assert llm.route("heart", [], DEPTS) == {"intent": "book", "specialty": "Cardiology", "reply": "Sure."}


def test_llm_route_drops_unknown_specialty_and_rejects_bad_intent():
    llm = _llm(_ok({"intent": "book", "specialty": "Astrology", "reply": "Sure."}))
    assert llm.route("x", [], DEPTS)["specialty"] is None
    llm = _llm(_ok({"intent": "hack", "specialty": None, "reply": "x"}))
    assert llm.route("x", [], DEPTS) is None


def test_llm_route_survives_garbage_and_http_errors():
    assert _llm(lambda r: httpx.Response(200, json={"message": {"content": "not json"}})).route("x", [], DEPTS) is None
    assert _llm(lambda r: httpx.Response(500)).route("x", [], DEPTS) is None
    assert _llm(lambda r: httpx.Response(200, json={})).route("x", [], DEPTS) is None


def test_llm_breaker_opens_so_a_dead_model_is_not_hammered():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    llm = _llm(handler, breaker=CircuitBreaker(failure_threshold=2, reset_seconds=60))
    for _ in range(5):
        assert llm.route("x", [], DEPTS) is None
    assert len(calls) == 2


def test_llm_is_up_reflects_tags_endpoint():
    assert _llm(lambda r: httpx.Response(200, json={"models": []})).is_up()
    assert not _llm(lambda r: httpx.Response(503)).is_up()


# ---- Voice client -------------------------------------------------------------------------------
def test_voice_transcribe_posts_audio_and_returns_text(tmp_path):
    audio = tmp_path / "clip.webm"
    audio.write_bytes(b"RIFFdata")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " hello there "})

    voice = Voice("http://voice.test", "kittentts", "whisperx", transport=httpx.MockTransport(handler))
    assert voice.transcribe(str(audio)) == "hello there"
    assert seen["path"] == "/v1/audio/transcriptions"
    assert b"clip.webm" in seen["body"] and str(tmp_path).encode() not in seen["body"]


def test_voice_failure_becomes_voice_unavailable(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    voice = Voice("http://voice.test", "k", "w", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(VoiceUnavailable):
        voice.transcribe(str(audio))
    with pytest.raises(VoiceUnavailable):
        voice.synthesize("hi", "v", str(tmp_path / "out.wav"))


def test_voice_synthesize_writes_the_wav(tmp_path):
    voice = Voice("http://voice.test", "k", "w",
                  transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"WAVDATA")))
    out = tmp_path / "out.wav"
    voice.synthesize("hello", "v", str(out))
    assert out.read_bytes() == b"WAVDATA"
