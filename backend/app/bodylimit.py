"""Reject oversized request bodies before the framework buffers them (pure ASGI, so it runs outermost)."""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_AUDIO_PATHS = {"/calls/audio", "/v1/calls/audio"}
MULTIPART_OVERHEAD = 64 * 1024  # boundary + headers around the audio bytes


class BodyLimit:
    """413 when Content-Length exceeds the route's cap; 411 when a body arrives with no length at all
    (chunked uploads cannot be bounded up front, and every legitimate client of this API sends a length)."""

    def __init__(self, app: ASGIApp, max_json_bytes: int, max_audio_bytes: int):
        self.app = app
        self.json_cap = max_json_bytes
        self.audio_cap = max_audio_bytes + MULTIPART_OVERHEAD

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _WRITE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        cap = self.audio_cap if scope["path"].rstrip("/") in _AUDIO_PATHS else self.json_cap
        length = headers.get(b"content-length")
        problem = None
        if length is None:
            if b"transfer-encoding" in headers:
                problem = (411, "length_required")
        elif not length.isdigit():
            problem = (400, "bad_content_length")
        elif int(length) > cap:
            problem = (413, "request_too_large")
        if problem:
            status, detail = problem
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
            return
        await self.app(scope, receive, send)
