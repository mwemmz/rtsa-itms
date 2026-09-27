"""In-process live-event hub for the SSE realtime feed.

Render runs the web tier as a single uvicorn process (no --workers), so an
in-memory pub/sub is sufficient: every mutation the web tier commits calls
``log_action`` in ``app/services/audit.py``, which publishes a change event
here. SSE subscribers registered via ``subscribe()`` receive it.

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

    def publish(self, payload: dict[str, Any]) -> None:
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