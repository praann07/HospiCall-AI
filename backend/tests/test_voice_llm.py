"""Voice and LLM adapters against httpx.MockTransport - no network, no Ollama, no OmniVoice."""
import json

import httpx
import pytest

from app.services.llm import INTENTS, LLMClient
from app.services.resilience import CircuitBreaker
from app.services.voice import Voice, VoiceUnavailable

DEPARTMENTS = ["Cardiology", "Neurology"]


def ollama_answer(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})


def llm(handler, **kw) -> LLMClient:
    return LLMClient("http://ollama", "test-model", transport=httpx.MockTransport(handler), **kw)


def voice(handler, **kw) -> Voice:
    return Voice("http://omni/", "kittentts", "whisperx", transport=httpx.MockTransport(handler), **kw)


# ---------------------------------------------------------------------------------------------- LLM
def test_llm_returns_validated_route():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return ollama_answer({"intent": "book", "specialty": "Cardiology", "reply": "Sure, let me help."})

    result = llm(handler).route("my heart feels funny", [], DEPARTMENTS)
    assert result == {"intent": "book", "specialty": "Cardiology", "reply": "Sure, let me help."}
    assert seen["model"] == "test-model" and seen["stream"] is False
    assert seen["format"]["properties"]["intent"]["enum"] == INTENTS  # output is schema-constrained
    assert "Cardiology, Neurology" in seen["messages"][0]["content"]


def test_llm_sends_only_recent_user_and_assistant_history():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return ollama_answer({"intent": "unclear", "specialty": None, "reply": "Could you rephrase?"})

    history = [{"role": "user", "content": f"u{i}"} for i in range(10)] + [{"role": "system", "content": "secret"}]
    llm(handler).route("hi", history, DEPARTMENTS)
    contents = [m["content"] for m in seen["messages"]]
    assert "secret" not in contents and "u0" not in contents and contents[-1] == "hi"


@pytest.mark.parametrize("bad", [
    {"intent": "launch_missiles", "specialty": None, "reply": "ok"},
    {"intent": "book", "specialty": None},
    ["not", "a", "dict"],
])
def test_llm_rejects_off_schema_answers(bad):
    assert llm(lambda r: ollama_answer(bad)).route("x", [], DEPARTMENTS) is None


def test_llm_drops_unknown_specialty():
    result = llm(lambda r: ollama_answer({"intent": "book", "specialty": "Astrology", "reply": "ok"})) \
        .route("x", [], DEPARTMENTS)
    assert result["specialty"] is None


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"message": {"content": "{{{"}}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
])
def test_llm_failures_return_none(handler):
    assert llm(handler).route("x", [], DEPARTMENTS) is None


def test_llm_breaker_opens_and_stops_calling_a_dead_server():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    client = llm(handler, breaker=CircuitBreaker(failure_threshold=2, reset_seconds=60))
    for _ in range(5):
        assert client.route("x", [], DEPARTMENTS) is None
    assert len(calls) == 2  # the remaining three calls failed fast


def test_llm_is_up():
    assert llm(lambda r: httpx.Response(200, json={"models": []})).is_up() is True
    assert llm(lambda r: httpx.Response(503)).is_up() is False


# ---------------------------------------------------------------------------------------------- Voice
def test_transcribe_posts_audio_and_returns_text(tmp_path):
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF....WAVE")
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), request.content
        return httpx.Response(200, json={"text": "  book a cardiologist  "})

    assert voice(handler).transcribe(str(wav)) == "book a cardiologist"
    assert seen["url"] == "http://omni/v1/audio/transcriptions"
    assert b'filename="clip.wav"' in seen["body"] and b"audio/wav" in seen["body"] and b"whisperx" in seen["body"]


def test_transcribe_labels_webm_correctly(tmp_path):
    clip = tmp_path / "clip.webm"
    clip.write_bytes(b"\x1aE\xdf\xa3")
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hi"})

    voice(handler).transcribe(str(clip))
    assert b"audio/webm" in seen["body"]


def test_transcribe_wraps_http_errors(tmp_path):
    clip = tmp_path / "c.wav"
    clip.write_bytes(b"x")
    with pytest.raises(VoiceUnavailable):
        voice(lambda r: httpx.Response(502)).transcribe(str(clip))
    with pytest.raises(VoiceUnavailable):
        voice(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))).transcribe(str(clip))


def test_transcribe_rejects_unexpected_payload(tmp_path):
    clip = tmp_path / "c.wav"
    clip.write_bytes(b"x")
    with pytest.raises(VoiceUnavailable):
        voice(lambda r: httpx.Response(200, json={"oops": 1})).transcribe(str(clip))


def test_synthesize_writes_wav(tmp_path):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=b"RIFFwavdata")

    out = tmp_path / "out.wav"
    assert voice(handler).synthesize("Hello there", "en-us-libritts-high", str(out)) == str(out)
    assert out.read_bytes() == b"RIFFwavdata"
    assert seen == {"model": "kittentts", "voice": "en-us-libritts-high", "input": "Hello there",
                    "response_format": "wav"}


def test_synthesize_failure_is_voice_unavailable(tmp_path):
    with pytest.raises(VoiceUnavailable):
        voice(lambda r: httpx.Response(500)).synthesize("x", "v", str(tmp_path / "o.wav"))


def test_voice_breaker_fails_fast_after_repeated_errors(tmp_path):
    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(500)

    v = voice(handler, breaker=CircuitBreaker(failure_threshold=2, reset_seconds=60))
    for _ in range(4):
        with pytest.raises(VoiceUnavailable):
            v.synthesize("x", "v", str(tmp_path / "o.wav"))
    assert len(hits) == 2


def test_voice_is_up_reports_reachability():
    assert voice(lambda r: httpx.Response(404)).is_up() is True  # any answer = server alive
    assert voice(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))).is_up() is False


def test_llm_may_flag_emergencies_and_self_harm():
    assert {"emergency", "self_harm"} <= set(INTENTS)
