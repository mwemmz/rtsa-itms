"""Shared state across instances (REDIS_URL): rate limits, stream tickets, live events, metrics.

There is no Redis in the test environment, so these use a small in-memory stand-in
that implements exactly the commands app/core/shared.py sends. Two "instances" are
simulated by clearing this process's in-memory state between calls: whatever
survives came from the shared store.
"""

import asyncio
import fnmatch
import queue
import threading
import time
from uuid import uuid4

import pytest

from app.core import shared


class FakePubSub:
    def __init__(self, server):
        self.server = server
        self.inbox: queue.Queue = queue.Queue()

    def subscribe(self, channel):
        self.server.subscribers.setdefault(channel, []).append(self.inbox)

    def get_message(self, timeout=0.0):
        try:
            return {"type": "message", "data": self.inbox.get(timeout=timeout)}
        except queue.Empty:
            return None

    def close(self):
        for boxes in self.server.subscribers.values():
            if self.inbox in boxes:
                boxes.remove(self.inbox)


class FakePipeline:
    def __init__(self, server):
        self.server, self.calls = server, []

    def __getattr__(self, name):
        def queue_call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return queue_call

    def execute(self):
        return [getattr(self.server, name)(*a, **kw) for name, a, kw in self.calls]


class FakeRedis:
    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.values: dict[str, tuple[str, float | None]] = {}
        self.subscribers: dict[str, list[queue.Queue]] = {}
        self.lock = threading.Lock()

    def pipeline(self):
        return FakePipeline(self)

    # sorted sets
    def zadd(self, key, mapping):
        with self.lock:
            self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zremrangebyscore(self, key, lo, hi):
        with self.lock:
            z = self.zsets.get(key, {})
            dead = [m for m, s in z.items() if (lo == "-inf" or s >= float(lo)) and s <= float(hi)]
            for m in dead:
                del z[m]
        return len(dead)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zrange(self, key, start, stop, withscores=False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        items = items[start:(None if stop == -1 else stop + 1)]
        return items if withscores else [m for m, _ in items]

    def zrem(self, key, member):
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    def expire(self, key, seconds):
        return True

    # strings
    def set(self, key, value, nx=False, ex=None):
        with self.lock:
            live = self.values.get(key)
            if live and live[1] is not None and live[1] < time.time():
                live = None
            if nx and live:
                return None
            self.values[key] = (value, time.time() + ex if ex else None)
            return True

    def mget(self, keys):
        return [self.values.get(k, (None, None))[0] for k in keys]

    def scan_iter(self, match="*"):
        return [k for k in list(self.values) if fnmatch.fnmatch(k, match)]

    def delete(self, key):
        self.zsets.pop(key, None)
        return 1 if self.values.pop(key, None) is not None else 0

    # pub/sub
    def publish(self, channel, message):
        boxes = list(self.subscribers.get(channel, []))
        for box in boxes:
            box.put(message)
        return len(boxes)

    def pubsub(self, ignore_subscribe_messages=False):
        return FakePubSub(self)


class BrokenRedis:
    """Every command fails, like an unreachable Redis."""

    def __getattr__(self, name):
        def fail(*a, **k):
            raise ConnectionError("redis unreachable")
        return fail


@pytest.fixture()
def fake_redis():
    fake = FakeRedis()
    shared.set_client(fake)
    yield fake
    shared.set_client(None)


def test_single_instance_by_default():
    assert not shared.enabled()


def test_login_throttle_is_shared_between_instances(fake_redis):
    from app.core import ratelimit

    key = f"login:10.0.{uuid4().int % 250}.1"
    for _ in range(ratelimit.MAX_ATTEMPTS):
        assert ratelimit.check_rate_limit(key)
        ratelimit.record_failure(key)
    with ratelimit._lock:
        ratelimit._attempts.clear()  # "another instance": nothing in its own memory
    assert not ratelimit.check_rate_limit(key)
    ratelimit.reset(key)
    assert ratelimit.check_rate_limit(key)


def test_agency_rate_limit_is_shared(fake_redis):
    key = f"rtsa:agency-rate:{uuid4()}"
    assert [shared.window_hit(key, 3, 60) for _ in range(3)] == [None, None, None]
    retry = shared.window_hit(key, 3, 60)
    assert retry is not None and 1 <= retry <= 60
    assert fake_redis.zcard(key) == 3  # the refused request isn't counted


def test_stream_ticket_is_single_use_across_instances(fake_redis):
    from app.api import events

    jti = uuid4().hex
    assert events._claim_ticket(jti, time.time() + 30)
    assert not events._claim_ticket(jti, time.time() + 30)
    assert jti not in events._used_tickets  # it lived in Redis, not this process


def test_live_events_travel_through_redis_to_every_instance(fake_redis):
    from app.services.events import hub

    loop = asyncio.new_event_loop()
    stop = None
    try:
        asyncio.set_event_loop(loop)
        hub.bind(loop)
        q = hub.subscribe()
        stop = hub.start_relay()
        time.sleep(0.2)  # let the relay subscribe
        hub.publish({"entity": "road_incident", "action": "report_incident", "entity_id": "here"})
        # and one published by a different instance straight into Redis
        shared.publish_event({"entity": "road_incident", "action": "resolve_incident", "entity_id": "elsewhere"})

        async def two():
            return [await asyncio.wait_for(q.get(), 2), await asyncio.wait_for(q.get(), 2)]

        got = loop.run_until_complete(two())
        assert sorted(e["entity_id"] for e in got) == ["elsewhere", "here"]
        hub.unsubscribe(q)
    finally:
        if stop is not None:
            stop.set()
        loop.close()


def test_metrics_merge_every_instance(fake_redis):
    from app.core import metrics

    route = f"GET /bench/{uuid4().hex[:6]}"
    metrics.record(route, 10.0, 200, 500)
    shared.publish_metrics({"instance": "other-instance", "started": time.time() - 100,
                            "samples": {route: [30.0, 50.0]}, "counts": {route: 2}, "errors": {route: 1},
                            "slow": {}})
    snap = metrics.snapshot()
    assert snap["instances"] == 2
    assert snap["routes"][route]["requests"] == 3 and snap["routes"][route]["errors_5xx"] == 1
    assert snap["routes"][route]["max_ms"] == 50.0


def test_redis_outage_falls_back_instead_of_failing_requests():
    from app.api import events
    from app.core import metrics, ratelimit

    shared.set_client(BrokenRedis())
    try:
        key = f"login:10.1.{uuid4().int % 250}.1"
        assert ratelimit.check_rate_limit(key)  # served from memory instead of raising
        ratelimit.record_failure(key)
        assert key in ratelimit._attempts
        jti = uuid4().hex
        assert events._claim_ticket(jti, time.time() + 30) and not events._claim_ticket(jti, time.time() + 30)
        assert metrics.snapshot()["instances"] == 1
    finally:
        shared.set_client(None)
        with ratelimit._lock:
            ratelimit._attempts.clear()
