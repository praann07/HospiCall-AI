"""A tiny circuit breaker so a dead dependency (Ollama, OmniVoice) fails fast instead of hanging every turn."""
import threading
import time
from collections.abc import Callable


class BreakerOpen(Exception):
    """Raised instead of calling a dependency that has been failing."""


class CircuitBreaker:
    """closed -> (N consecutive failures) -> open -> (after reset_seconds) -> half-open -> closed on success."""

    def __init__(self, failure_threshold: int = 3, reset_seconds: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if self._clock() - self._opened_at >= self.reset_seconds else "open"

    def call(self, fn: Callable, *args, **kwargs):
        if self.state == "open":
            raise BreakerOpen("dependency temporarily disabled after repeated failures")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()  # (re)open; a failed half-open probe restarts the timer

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
