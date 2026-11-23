"""Process-local counters and latency percentiles, exposed as JSON and Prometheus text."""
import threading
from collections import Counter, deque

WINDOW = 2000  # latency samples kept per path


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[idx], 2)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: Counter = Counter()
        self._latency: dict[str, deque] = {}

    def inc(self, name: str, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += 1

    def observe_turn(self, path: str, intent: str, latency_ms: float) -> None:
        self.inc("hospicall_turns_total", path=path, intent=intent)
        with self._lock:
            self._latency.setdefault(path, deque(maxlen=WINDOW)).append(latency_ms)

    def snapshot(self) -> dict:
        with self._lock:
            latency = {p: {"count": len(v), "p50_ms": percentile(list(v), 50), "p95_ms": percentile(list(v), 95)}
                       for p, v in self._latency.items()}
            counters = [{"name": n, "labels": dict(lbl), "value": v} for (n, lbl), v in self._counters.items()]
        return {"counters": counters, "latency": latency}

    def prometheus(self) -> str:
        snap = self.snapshot()
        lines = ["# TYPE hospicall_turns_total counter"]
        for c in snap["counters"]:
            labels = ",".join(f'{k}="{v}"' for k, v in sorted(c["labels"].items()))
            lines.append(f'{c["name"]}{{{labels}}} {c["value"]}' if labels else f'{c["name"]} {c["value"]}')
        lines.append("# TYPE hospicall_turn_latency_ms summary")
        for path, s in snap["latency"].items():
            lines.append(f'hospicall_turn_latency_ms{{path="{path}",quantile="0.5"}} {s["p50_ms"]}')
            lines.append(f'hospicall_turn_latency_ms{{path="{path}",quantile="0.95"}} {s["p95_ms"]}')
            lines.append(f'hospicall_turn_latency_ms_count{{path="{path}"}} {s["count"]}')
        return "\n".join(lines) + "\n"
