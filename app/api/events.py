"""Server-sent events (SSE) endpoint streaming live change notifications.

The single-page app opens an ``EventSource`` against ``/api/events/stream``.
Because EventSource cannot set request headers, authentication accepts the
access token either as a query parameter (``?token=``) or the standard
``Authorization: Bearer`` header (used by curl/tests and future clients).

The stream is a plain ``text/event-stream``; starlette's GZipMiddleware
excludes that media type, so events flush immediately. Heartbeat comments are
sent every 25s so proxies don't drop an idle connection.
"""

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_user_from_token
from app.models.user import User
from app.services.events import hub

router = APIRouter(prefix="/api/events", tags=["Realtime"])

HEARTBEAT_SECONDS = 25


def _auth_dependency(
    token: str | None = Query(default=None, description="Access token (EventSource cannot set headers)"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    raw = authorization.removeprefix("Bearer ").strip() if authorization else (token or "")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return get_user_from_token(raw, db)


@router.get("/stream")
async def event_stream(_user: User = Depends(_auth_dependency)) -> StreamingResponse:
    queue = hub.subscribe()

    return StreamingResponse(
        source(queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def source(queue: asyncio.Queue) -> AsyncIterator[str]:
    """The SSE frame stream. Separated from the route so it is unit-testable."""
    # Flush headers immediately: some proxies wait for the first byte
    # before treating the connection as established.
    yield ": connected\n\nevent: hello\ndata: {}\n\n"
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(payload, default=str)}\n\n"
    finally:
        hub.unsubscribe(queue)