"""Server-sent events (SSE) endpoint streaming live change notifications.

The single-page app opens an ``EventSource`` against ``/api/events/stream``.
EventSource cannot set request headers, so the browser authenticates through
the URL - and URLs land in access logs (uvicorn's, Render's, any proxy's). The
access token therefore never goes there: the app first calls
``POST /api/events/ticket`` (normal Bearer auth) and opens
``/api/events/stream?ticket=...`` with a ticket that only opens the stream,
expires after ``STREAM_TICKET_SECONDS`` and works once. Non-browser clients
(curl, tests) can still send ``Authorization: Bearer <access token>``.

The stream is a plain ``text/event-stream``; starlette's GZipMiddleware
excludes that media type, so events flush immediately. Heartbeat comments are
sent every 25s so proxies don't drop an idle connection.
"""

import asyncio
import json
import secrets
import threading
import time
from datetime import timedelta
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_token,
    enforce_staff_mfa,
    get_current_user,
    get_user_from_token,
)
from app.models.user import User
from app.services.events import hub

router = APIRouter(prefix="/api/events", tags=["Realtime"])

HEARTBEAT_SECONDS = 25
STREAM_TICKET_SECONDS = 30

# Ticket ids already used to open a stream, with their expiry. Tickets expire in
# seconds, so this stays tiny. Per-process, like the hub itself (see
# app/services/events.py) - fine for Render's single web process.
_used_tickets: dict[str, float] = {}
_used_lock = threading.Lock()


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _claim_ticket(jti: str, expires_at: float) -> bool:
    """Mark a ticket as used. False if it had already been used."""
    now = time.time()
    with _used_lock:
        for stale in [k for k, exp in _used_tickets.items() if exp < now]:
            del _used_tickets[stale]
        if jti in _used_tickets:
            return False
        _used_tickets[jti] = expires_at
        return True


@router.post("/ticket")
def stream_ticket(current_user: User = Depends(get_current_user)) -> dict:
    """Issue a short-lived, single-use ticket for opening the event stream.

    Put this in the stream URL instead of the access token: it can't be used on
    any other endpoint, expires after ``STREAM_TICKET_SECONDS`` and works once.
    """
    ticket = create_access_token(
        {
            "sub": str(current_user.id),
            "sid": str(current_user._session_id),  # type: ignore[attr-defined]
            "typ": "stream",
            "jti": secrets.token_urlsafe(12),
        },
        expires_delta=timedelta(seconds=STREAM_TICKET_SECONDS),
    )
    return {"ticket": ticket, "expires_in": STREAM_TICKET_SECONDS}


def stream_user(db: Session, ticket: str | None, authorization: str | None) -> User:
    """Authenticate a stream request by Bearer access token or single-use ticket."""
    if authorization:
        return get_user_from_token(authorization.removeprefix("Bearer ").strip(), db)
    if not ticket:
        raise _unauthorized()
    try:
        claims = decode_token(ticket)
    except JWTError:
        raise _unauthorized("Stream ticket is invalid or has expired")
    jti = claims.get("jti")
    if claims.get("typ") != "stream" or not jti:
        raise _unauthorized("Stream ticket is invalid or has expired")
    # Validate the session (signed out, idle, blocked device...) before burning the ticket.
    user = get_user_from_token(ticket, db, typ="stream")
    if not _claim_ticket(jti, float(claims.get("exp", time.time() + STREAM_TICKET_SECONDS))):
        raise _unauthorized("Stream ticket has already been used")
    return user


def _auth_dependency(
    request: Request,
    ticket: str | None = Query(default=None, description="Single-use ticket from POST /api/events/ticket"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    user = stream_user(db, ticket, authorization)
    enforce_staff_mfa(user, request.url.path, db)
    return user


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