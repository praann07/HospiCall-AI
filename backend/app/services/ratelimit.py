"""Tiny in-memory sliding-window rate limiter (per client key). Swap for Redis on multi-worker deployments."""
import threading
import time
from collections import deque
from collections.abc import Callable

MAX_KEYS = 10_000  # distinct clients tracked at once; memory stays bounded however many addresses probe us


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = MAX_KEYS):
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = {}
        self._next_prune = 0.0

    def allow(self, key: str) -> bool:
        """Record a request from `key`; False when it exceeded the limit for the current window."""
        if self.limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            if key not in self._hits and len(self._hits) >= self.max_keys:
                self._make_room(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _make_room(self, now: float) -> None:
        """Drop idle clients (at most once per window, so a flood of new keys cannot force O(n) work per
        request), then evict the oldest-seen client if that was not enough."""
        if now >= self._next_prune:
            self._next_prune = now + self.window
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
        if len(self._hits) >= self.max_keys:
            self._hits.pop(next(iter(self._hits)))
