"""Request metrics (per route latency percentiles, error counts).

Kept deliberately small and dependency free. Each process records its own
window in memory - no extra work per request. With ``REDIS_URL`` set, each
instance also publishes its window every ``PUBLISH_SECONDS`` and ``snapshot()``
merges every live instance's figures, so ``/api/system/metrics`` describes the
whole deployment rather than whichever instance served the request. The shape
maps 1:1 onto Prometheus/APM histogram summaries if you outgrow this.
"""

import threading
import time
from collections import defaultdict, deque

from app.core import shared
from app.core.logging import get_logger

logger = get_logger("metrics")
PUBLISH_SECONDS = 10

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


def _local_state() -> dict:
    with _lock:
        return {
            "instance": shared.INSTANCE_ID,
            "started": _started,
            "samples": {route: list(dq) for route, dq in _samples.items()},
            "counts": dict(_counts),
            "errors": dict(_errors),
            "slow": dict(_slow),
        }


def _merge(states: list[dict]) -> dict:
    samples: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    slow: dict[str, int] = defaultdict(int)
    for st in states:
        for route, vals in st["samples"].items():
            samples[route].extend(vals)
        for target, source in ((counts, st["counts"]), (errors, st["errors"]), (slow, st["slow"])):
            for route, n in source.items():
                target[route] += n
    routes = {}
    everything: list[float] = []
    for route, vals in samples.items():
        everything.extend(vals)
        routes[route] = {"requests": counts[route], "errors_5xx": errors[route], "slow": slow[route], **_summary(vals)}
    return {
        "uptime_seconds": int(time.time() - min(st["started"] for st in states)),
        "instances": len(states),
        "total_requests": sum(counts.values()),
        "total_5xx": sum(errors.values()),
        "overall": _summary(everything),
        "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1]["p95_ms"])),
    }


def snapshot() -> dict:
    """Metrics for this instance, or for every live instance when Redis is configured."""
    local = _local_state()
    if not shared.enabled():
        return _merge([local])
    try:
        others = [st for st in shared.all_metrics() if st.get("instance") != local["instance"]]
    except Exception as exc:  # noqa: BLE001 - Redis down: still show this instance
        logger.error("Could not read other instances' metrics (%s)", exc)
        others = []
    return _merge([local, *others])


def start_publisher() -> threading.Event | None:
    """With Redis configured, publish this instance's window every PUBLISH_SECONDS."""
    if not shared.enabled():
        return None
    stop = threading.Event()

    def run() -> None:
        while not stop.is_set():
            try:
                shared.publish_metrics(_local_state())
            except Exception as exc:  # noqa: BLE001 - metrics must never take the app down
                logger.error("Could not publish metrics to Redis (%s)", exc)
            stop.wait(PUBLISH_SECONDS)

    threading.Thread(target=run, name="metrics-publisher", daemon=True).start()
    return stop


def reset() -> None:
    with _lock:
        _samples.clear()
        _counts.clear()
        _errors.clear()
        _slow.clear()
