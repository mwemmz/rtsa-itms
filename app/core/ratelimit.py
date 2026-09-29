"""Sliding-window rate limiter for brute-force protection (login, sign-up).

In memory for a single instance; shared through Redis when ``REDIS_URL`` is set,
so every instance behind a load balancer sees the same attempt counts
(see ``app/core/shared.py``).
"""

import time
import threading

from app.core import shared

_lock = threading.Lock()
_attempts: dict[str, list[float]] = {}

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300  # 5 minutes
LOCKOUT_SECONDS = 600  # 10 minutes


def _prune(now: float) -> None:
    stale = [k for k, v in _attempts.items() if v and (now - v[-1]) > WINDOW_SECONDS]
    for k in stale:
        del _attempts[k]


def check_rate_limit(key: str, max_attempts: int = MAX_ATTEMPTS) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    if shared.enabled():
        try:
            return shared.window_count(_redis_key(key), WINDOW_SECONDS) < max_attempts
        except Exception as exc:  # noqa: BLE001
            shared.degraded("rate limiting", exc)
    now = time.time()
    with _lock:
        _prune(now)
        stamps = _attempts.get(key, [])
        # If there's a recent failed attempt beyond the window, we just push.
        stamps = [s for s in stamps if (now - s) < WINDOW_SECONDS]
        if len(stamps) >= max_attempts:
            return False
        _attempts[key] = stamps
        return True


def _redis_key(key: str) -> str:
    return f"rtsa:ratelimit:{key}"


def record_failure(key: str) -> None:
    if shared.enabled():
        try:
            shared.window_add(_redis_key(key), WINDOW_SECONDS)
            return
        except Exception as exc:  # noqa: BLE001
            shared.degraded("rate limiting", exc)
    now = time.time()
    with _lock:
        _attempts.setdefault(key, []).append(now)


def reset(key: str) -> None:
    if shared.enabled():
        try:
            shared.window_clear(_redis_key(key))
            return
        except Exception as exc:  # noqa: BLE001
            shared.degraded("rate limiting", exc)
    with _lock:
        _attempts.pop(key, None)