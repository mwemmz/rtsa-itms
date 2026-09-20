"""In-process request metrics (per route latency percentiles, error counts).

Kept deliberately small and dependency free. Each worker process keeps its own
window; for multi-instance deployments export the same numbers to Prometheus /
your APM (the ``snapshot`` shape maps 1:1 onto histogram summaries).
"""

import threading
import time
from collections import defaultdict, deque

WINDOW = 1000  # samples kept per route

_lock = threading.Lock()
_started = time.time()
_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))
_counts: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)
_slow: dict[str, int] = defaultdict(int)


def record(route: str, elapsed_ms: float, status_code: int, slow_threshold_ms: float) -> None:
    with _lock:
        _samples[route].append(elapsed_ms)
        _counts[route] += 1
        if status_code >= 500:
            _errors[route] += 1
        if elapsed_ms > slow_threshold_ms:
            _slow[route] += 1


def _pct(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(p / 100 * len(sorted_values) + 0.5)) - 1))
    return sorted_values[idx]


def _summary(values: list[float]) -> dict:
    s = sorted(values)
    return {
        "avg_ms": round(sum(s) / len(s), 2) if s else 0.0,
        "p50_ms": round(_pct(s, 50), 2),
        "p95_ms": round(_pct(s, 95), 2),
        "p99_ms": round(_pct(s, 99), 2),
        "max_ms": round(s[-1], 2) if s else 0.0,
    }


def snapshot() -> dict:
    with _lock:
        routes = {}
        everything: list[float] = []
        for route, dq in _samples.items():
            vals = list(dq)
            everything.extend(vals)
            routes[route] = {"requests": _counts[route], "errors_5xx": _errors[route],
                             "slow": _slow[route], **_summary(vals)}
        total = sum(_counts.values())
        errors = sum(_errors.values())
    return {
        "uptime_seconds": int(time.time() - _started),
        "total_requests": total,
        "total_5xx": errors,
        "overall": _summary(everything),
        "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1]["p95_ms"])),
    }


def reset() -> None:
    with _lock:
        _samples.clear()
        _counts.clear()
        _errors.clear()
        _slow.clear()
