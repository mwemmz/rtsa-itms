import os
import sys
import threading
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TRUST_PROXY_HEADERS"] = "true"

import asyncio  # noqa: E402

import pytest  # noqa: E402
import app.models  # noqa: F401, E402  # register all models on Base.metadata
from app.core.database import Base, engine  # noqa: E402

Base.metadata.create_all(bind=engine)

from app.models.user import UserRole  # noqa: E402
from app.services.events import hub  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _login(client) -> str:
    from tests.conftest import create_user

    email, _ = create_user(UserRole.ADMIN.value)
    resp = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_hub_publish_reaches_subscriber():
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        hub.bind(loop)
        q = hub.subscribe()
        received = {}

        async def run():
            received["item"] = await asyncio.wait_for(q.get(), timeout=1)

        task = asyncio.ensure_future(run())
        hub.publish({"entity": "vehicle", "action": "create", "entity_id": "abc"})
        loop.run_until_complete(task)
        assert received["item"]["entity"] == "vehicle"
        assert received["item"]["action"] == "create"
        hub.unsubscribe(q)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception:
            pass


def test_stream_requires_auth(client):
    resp = client.get("/api/events/stream")
    assert resp.status_code == 401


def _ticket(client, token) -> str:
    resp = client.post("/api/events/ticket", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["expires_in"] == 30
    return resp.json()["ticket"]


def test_access_token_is_no_longer_accepted_in_the_url(client):
    token = _login(client)
    # the old ?token= parameter is gone - access tokens must never sit in a URL
    assert client.get("/api/events/stream", params={"token": token}).status_code == 401
    # nor does an access token work where a ticket is expected
    assert client.get("/api/events/stream", params={"ticket": token}).status_code == 401


def test_stream_ticket_opens_the_stream_once():
    from fastapi import HTTPException

    from app.api.events import stream_user
    from app.core.database import SessionLocal
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        token = _login(c)
        ticket = _ticket(c, token)
    db = SessionLocal()
    try:
        user = stream_user(db, ticket, None)
        assert user.role == UserRole.ADMIN
        with pytest.raises(HTTPException) as again:
            stream_user(db, ticket, None)
        assert again.value.status_code == 401 and "already been used" in again.value.detail
    finally:
        db.close()


def test_stream_ticket_is_useless_anywhere_else(client):
    token = _login(client)
    ticket = _ticket(client, token)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401
    # and a ticket can't mint another ticket
    assert client.post("/api/events/ticket", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401


def test_expired_or_signed_out_tickets_are_refused(client):
    from datetime import timedelta

    from app.core.security import create_access_token, decode_token

    token = _login(client)
    claims = decode_token(token)
    expired = create_access_token({"sub": claims["sub"], "sid": claims["sid"], "typ": "stream", "jti": "x1"},
                                  expires_delta=timedelta(seconds=-5))
    resp = client.get("/api/events/stream", params={"ticket": expired})
    assert resp.status_code == 401 and "expired" in resp.json()["detail"]

    ticket = _ticket(client, token)
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert client.get("/api/events/stream", params={"ticket": ticket}).status_code == 401


def test_bearer_header_still_authenticates_the_stream(client):
    from app.api.events import stream_user
    from app.core.database import SessionLocal

    token = _login(client)
    db = SessionLocal()
    try:
        assert stream_user(db, None, f"Bearer {token}").role == UserRole.ADMIN
    finally:
        db.close()


def test_stream_generator_emits_published_event():
    from app.api.events import source

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        hub.bind(loop)
        q = hub.subscribe()
        frames = []

        async def consume():
            i = 0
            async for frame in source(q):
                frames.append(frame)
                i += 1
                if i >= 3:  # hello + blank + one data frame
                    break

        async def produce():
            await asyncio.sleep(0.02)
            hub.publish({"entity": "challan", "action": "generate_challan", "entity_id": str(uuid4())})

        loop.run_until_complete(asyncio.gather(consume(), produce()))
        assert any('"entity": "challan"' in f and '"generate_challan"' in f for f in frames)
    finally:
        hub.unsubscribe(q)
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception:
            pass


def test_mutating_log_action_broadcasts():
    """A mutating audit write publishes to the hub, so connected tabs refresh."""
    from app.core.database import SessionLocal
    from app.services.audit import log_action

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        hub.bind(loop)
        q = hub.subscribe()
        received = {}

        async def run():
            received["item"] = await asyncio.wait_for(q.get(), timeout=1)

        task = asyncio.ensure_future(run())
        db = SessionLocal()
        try:
            entry = log_action(db, "create", "vehicle", "veh-1", "Registered AB1234", None)
            assert entry.entity_type == "vehicle"
        finally:
            db.rollback()
            db.close()
        loop.run_until_complete(task)
        assert received["item"]["entity"] == "vehicle"
        assert received["item"]["action"] == "create"
        hub.unsubscribe(q)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception:
            pass


def test_endpoint_streams_via_asgi(client):
    """Run a real uvicorn server and read SSE frames over HTTP."""
    import json
    import socket
    import time

    import httpx
    import uvicorn

    token = _login(client)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.1)
    assert server.started, "uvicorn did not start"

    event_id = str(uuid4())

    stop = threading.Event()

    def publish():
        # Keep publishing until the reader has it: one publish can land before the
        # stream subscribes (slow machine), and would then be lost for good.
        while not stop.wait(0.5):
            hub.publish({"entity": "road_incident", "action": "report_incident", "entity_id": event_id})

    th = threading.Thread(target=publish, daemon=True)
    th.start()

    received = None
    try:
        with httpx.Client(timeout=None) as hc:
            with hc.stream("GET", f"http://127.0.0.1:{port}/api/events/stream",
                           params={"ticket": _ticket(client, token)}) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line[6:])
                    if payload.get("entity") == "road_incident":
                        received = payload
                        break
    finally:
        stop.set()
        server.should_exit = True
        thread.join(timeout=10)

    assert received is not None, "stream never delivered the road_incident frame"
    assert received["entity_id"] == event_id