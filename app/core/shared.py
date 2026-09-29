"""State that has to agree across app instances, backed by Redis when ``REDIS_URL`` is set.

One process (Render's current setup) keeps everything in memory, and each module
below keeps its original in-memory code for that case. Behind a load balancer
with several instances, per-process state goes wrong: each instance would count
login attempts separately, accept a stream ticket once *each*, only deliver live
updates to its own connections, and report only its own metrics. Setting
``REDIS_URL`` routes those through Redis instead:

* sliding-window counters - login and sign-up throttles, agency rate limits
  (``app.core.ratelimit``, ``app.services.integration``)
* one-time markers - single-use stream tickets (``app.api.events``)
* pub/sub - live-update events reach every instance's connections
  (``app.services.events``)
* metrics - each instance publishes its window; ``/api/system/metrics`` merges
  them (``app.core.metrics``)

Everything else that must be shared (sessions, lockouts, settings, the ledger)
already lives in the database. Short-lived read caches (permissions, settings:
5 s; dashboard: ``REPORT_CACHE_SECONDS``) stay per instance; they only delay a
change by that long.
"""

import json
import threading
import time
import uuid
from typing import Any, Callable

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("shared")

INSTANCE_ID = uuid.uuid4().hex[:12]
EVENTS_CHANNEL = "rtsa:events"

_client: Any = None
_client_lock = threading.Lock()


def set_client(client: Any) -> None:
    """Use this Redis client (tests pass a fake); None goes back to REDIS_URL."""
    global _client
    with _client_lock:
        _client = client


def redis() -> Any:
    """The shared Redis client, or None when running as a single instance."""
    global _client
    if _client is not None:
        return _client
    if not settings.REDIS_URL:
        return None
    with _client_lock:
        if _client is None:
            import redis as redis_lib  # only needed when REDIS_URL is set

            _client = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True,
                                               socket_timeout=5, socket_connect_timeout=5,
                                               health_check_interval=30)
    return _client


def enabled() -> bool:
    return redis() is not None


_last_degraded_log = 0.0


def degraded(what: str, exc: Exception) -> None:
    """Note that Redis failed and ``what`` fell back to this instance's memory.

    Callers keep working per-instance rather than failing requests (a Redis blip
    must not take logins down). Logged at most once a minute.
    """
    global _last_degraded_log
    now = time.monotonic()
    if now - _last_degraded_log > 60:
        _last_degraded_log = now
        logger.error("Redis unavailable (%s); %s is falling back to this instance only", exc, what)


# --- sliding-window counters ------------------------------------------------------------------

def _trim(pipe, key: str, now: float, window: float) -> None:
    pipe.zremrangebyscore(key, "-inf", now - window)


def window_count(key: str, window: float) -> int:
    r = redis()
    now = time.time()
    pipe = r.pipeline()
    _trim(pipe, key, now, window)
    pipe.zcard(key)
    return int(pipe.execute()[-1])


def window_add(key: str, window: float) -> None:
    r = redis()
    now = time.time()
    pipe = r.pipeline()
    _trim(pipe, key, now, window)
    pipe.zadd(key, {f"{now:.6f}:{uuid.uuid4().hex[:8]}": now})
    pipe.expire(key, int(window) + 1)
    pipe.execute()


def window_hit(key: str, limit: int, window: float) -> float | None:
    """Count one event if the window has room. Returns None if allowed, else seconds to wait.

    Adds first and then checks, so two instances racing for the last slot can't
    both get it (at worst both are refused, which is the safe side).
    """
    r = redis()
    now = time.time()
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
    pipe = r.pipeline()
    _trim(pipe, key, now, window)
    pipe.zadd(key, {member: now})
    pipe.expire(key, int(window) + 1)
    pipe.zcard(key)
    pipe.zrange(key, 0, 0, withscores=True)
    *_, count, oldest = pipe.execute()
    if int(count) <= limit:
        return None
    r.zrem(key, member)
    first = oldest[0][1] if oldest else now
    return max(1.0, window - (now - float(first)))


def window_clear(key: str) -> None:
    redis().delete(key)


# --- one-time markers -------------------------------------------------------------------------

def claim_once(key: str, ttl_seconds: int) -> bool:
    """True the first time a key is claimed (across all instances), False afterwards."""
    return bool(redis().set(key, "1", nx=True, ex=max(1, ttl_seconds)))


# --- pub/sub for live updates -----------------------------------------------------------------

def publish_event(payload: dict) -> None:
    redis().publish(EVENTS_CHANNEL, json.dumps(payload, default=str))


def start_event_listener(deliver: Callable[[dict], None]) -> threading.Event:
    """Relay events published by any instance to ``deliver`` until the returned event is set."""
    stop = threading.Event()

    def run() -> None:
        backoff = 1.0
        while not stop.is_set():
            pubsub = None
            try:
                pubsub = redis().pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(EVENTS_CHANNEL)
                backoff = 1.0
                while not stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message and message.get("type") == "message":
                        try:
                            deliver(json.loads(message["data"]))
                        except (ValueError, TypeError):
                            logger.warning("Ignoring malformed live event from Redis")
            except Exception as exc:  # noqa: BLE001 - keep relaying after Redis blips
                logger.error("Live-event relay lost Redis (%s); retrying in %.0fs", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:  # noqa: BLE001
                        pass

    threading.Thread(target=run, name="live-event-relay", daemon=True).start()
    return stop


# --- metrics ----------------------------------------------------------------------------------

METRICS_PREFIX = "rtsa:metrics:"
METRICS_TTL = 60  # an instance that stops publishing drops out of the totals after this


def publish_metrics(state: dict) -> None:
    redis().set(METRICS_PREFIX + INSTANCE_ID, json.dumps(state), ex=METRICS_TTL)


def all_metrics() -> list[dict]:
    r = redis()
    keys = list(r.scan_iter(match=METRICS_PREFIX + "*"))
    return [json.loads(v) for v in (r.mget(keys) if keys else []) if v]
