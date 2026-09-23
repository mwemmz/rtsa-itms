"""In-process live-event hub for the SSE realtime feed.

Render runs the web tier as a single uvicorn process (no --workers), so an
in-memory pub/sub is sufficient: every mutation the web tier commits calls
``log_action`` in ``app/services/audit.py``, which publishes a change event
here. SSE subscribers registered via ``subscribe()`` receive it.

``publish`` is safe to call from any thread (sync endpoints run in Starlette's
threadpool): it hands the payload to the owning event loop with
``call_soon_threadsafe``. If the server is running without a bound loop (e.g.
in tests) publishes are dropped silently.
"""

import asyncio
import threading
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber queue bound to the running loop."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            subscribers = list(self._subscribers)
        if not subscribers:
            return
        for q in subscribers:
            if q.full():
                try:
                    q.get_nowait()  # drop oldest for an unusually slow consumer
                except asyncio.QueueEmpty:
                    pass
            try:
                loop.call_soon_threadsafe(q.put_nowait, dict(payload))
            except RuntimeError:  # loop already closed
                return


hub = EventHub()