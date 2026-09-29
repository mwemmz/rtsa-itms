"""Live-event hub for the SSE realtime feed.

Every mutation the web tier commits calls ``log_action`` in
``app/services/audit.py``, which publishes a change event here. SSE subscribers
registered via ``subscribe()`` receive it. With one instance (Render's current
setup) that's all in memory. With ``REDIS_URL`` set, ``publish`` goes through a
Redis channel instead and a relay thread on every instance hands each event to
its own subscribers - so a change made on one instance reaches connections held
by the others (see ``app/core/shared.py``).

``publish`` is safe to call from any thread (sync endpoints run in Starlette's
threadpool): it hands the payload to the owning event loop with
``call_soon_threadsafe``. If the server is running without a bound loop (e.g.
in tests) publishes are dropped silently.

Each subscriber can pass a ``view``: a function that returns the event as that
subscriber may see it, or None to skip it (see ``app/services/event_visibility.py``).
It runs at publish time, so events a viewer can't see never enter their queue.
"""

import asyncio
import threading
from typing import Any, Callable

from app.core import shared
from app.core.logging import get_logger

logger = get_logger("events")

View = Callable[[dict[str, Any]], "dict[str, Any] | None"]


class EventHub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[asyncio.Queue, View | None] = {}
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, view: View | None = None) -> asyncio.Queue:
        """Register a new subscriber queue bound to the running loop."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers[q] = view
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(q, None)

    def has_subscribers(self) -> bool:
        with self._lock:
            return bool(self._subscribers)

    def wants_events(self) -> bool:
        """Whether publishing is worth it: local listeners, or other instances via Redis."""
        return self.has_subscribers() or shared.enabled()

    def publish(self, payload: dict[str, Any]) -> None:
        if shared.enabled():
            try:
                shared.publish_event(payload)  # the relay delivers it here and on every other instance
                return
            except Exception as exc:  # noqa: BLE001 - Redis down: still serve this instance's viewers
                logger.error("Could not publish live event to Redis (%s); delivering locally only", exc)
        self.deliver(payload)

    def start_relay(self) -> threading.Event | None:
        """With Redis configured, relay events from all instances to this one's subscribers."""
        return shared.start_event_listener(self.deliver) if shared.enabled() else None

    def deliver(self, payload: dict[str, Any]) -> None:
        """Hand an event to this instance's subscribers, each through its own view."""
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            subscribers = list(self._subscribers.items())
        if not subscribers:
            return
        for q, view in subscribers:
            try:
                item = view(payload) if view else dict(payload)
            except Exception:  # noqa: BLE001 - a broken filter must not leak or crash; skip
                continue
            if item is None:
                continue
            if q.full():
                try:
                    q.get_nowait()  # drop oldest for an unusually slow consumer
                except asyncio.QueueEmpty:
                    pass
            try:
                loop.call_soon_threadsafe(q.put_nowait, item)
            except RuntimeError:  # loop already closed
                return


hub = EventHub()