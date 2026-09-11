"""Simple in-memory rate limiter for brute-force protection.

The spec calls for brute-force protection on login. In production this would
be backed by Redis; here an in-process dict with TTL is sufficient for a
single-instance deploy on Render.
"""

import time
import threading

_lock = threading.Lock()
_attempts: dict[str, list[float]] = {}

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300  # 5 minutes
LOCKOUT_SECONDS = 600  # 10 minutes


def _prune(now: float) -> None:
    stale = [k for k, v in _attempts.items() if v and (now - v[-1]) > WINDOW_SECONDS]
    for k in stale:
        del _attempts[k]


def check_rate_limit(key: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.time()
    with _lock:
        _prune(now)
        stamps = _attempts.get(key, [])
        # If there's a recent failed attempt beyond the window, we just push.
        stamps = [s for s in stamps if (now - s) < WINDOW_SECONDS]
        if len(stamps) >= MAX_ATTEMPTS:
            return False
        _attempts[key] = stamps
        return True


def record_failure(key: str) -> None:
    now = time.time()
    with _lock:
        _attempts.setdefault(key, []).append(now)


def reset(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)