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

    def publish():
        time.sleep(1.0)
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
        server.should_exit = True
        thread.join(timeout=10)

    assert received is not None, "stream never delivered the road_incident frame"
    assert received["entity_id"] == event_id


# ============================ who sees which event ============================

def _viewer(user_id="u-self", staff=False, perms=()):
    from app.services.event_visibility import Viewer

    return Viewer(user_id=user_id, is_staff=staff, permissions=frozenset(perms))


def _event(entity, action="update", audience=(), actor="u-actor"):
    return {"entity": entity, "action": action, "entity_id": "e1", "actor_id": actor,
            "at": "now", "audience": list(audience)}


def test_visibility_policy():
    from app.core.permissions import PERMISSIONS
    from app.services.event_visibility import visible_event

    citizen = _viewer()
    officer = _viewer("u-off", staff=True)
    admin = _viewer("u-adm", staff=True, perms=PERMISSIONS)

    # public: road incidents and broadcast announcements reach everyone
    assert visible_event(citizen, _event("road_incident", "report_incident"))
    assert visible_event(citizen, _event("notification", "broadcast"))
    # someone else's sign-in: only people who manage security see it
    other_login = _event("session", "login", audience=["u-other"])
    assert visible_event(citizen, other_login) is None
    assert visible_event(officer, other_login) is None
    assert visible_event(admin, other_login)
    # the viewer's own records always reach them
    assert visible_event(citizen, _event("challan", "generate_challan", audience=["u-self"]))
    assert visible_event(citizen, _event("session", "login", audience=["u-self"]))
    # operational records: staff yes, unrelated citizens no
    fine = _event("challan", "generate_challan", audience=["u-other"])
    assert visible_event(officer, fine) and visible_event(citizen, fine) is None
    # admin records need the matching permission, not just a staff role
    assert visible_event(officer, _event("setting", "update_setting")) is None
    assert visible_event(_viewer(staff=True, perms={"settings:manage"}), _event("setting", "update_setting"))
    # unclassified entity types stay admin-only
    assert visible_event(officer, _event("something_new")) is None
    assert visible_event(admin, _event("something_new"))
    # who did it is only shown to auditors; the audience list is never sent
    seen = visible_event(officer, fine)
    assert "actor_id" not in seen and "audience" not in seen
    assert visible_event(admin, fine)["actor_id"] == "u-actor"


def test_audience_resolves_record_owners():
    from datetime import datetime, timedelta

    from app.core.database import SessionLocal
    from app.models.enforcement import Challan, Violation, ViolationType
    from app.models.vehicle import Vehicle
    from app.services.event_visibility import audience_for
    from tests.conftest import create_user

    _, owner_id = create_user("citizen")
    _, officer_id = create_user("officer")
    db = SessionLocal()
    try:
        v = Vehicle(registration_number=f"AU{uuid4().hex[:6].upper()}", owner_name="Own Er", owner_id_number="1",
                    make="Toyota", model="Hilux", year=2020, user_id=owner_id)
        db.add(v)
        db.flush()
        viol = Violation(vehicle_id=v.id, violation_type=ViolationType.SPEEDING, location="Great East Rd")
        db.add(viol)
        db.flush()
        c = Challan(reference=f"CH-{uuid4().hex[:8].upper()}", violation_id=viol.id, vehicle_id=v.id,
                    penalty_amount=500, due_date=datetime.utcnow() + timedelta(days=10))
        db.add(c)
        db.flush()
        assert audience_for(db, "challan", str(c.id), officer_id) == sorted({str(owner_id), str(officer_id)})
        assert audience_for(db, "vehicle", str(v.id), None) == [str(owner_id)]
        assert audience_for(db, "user", str(owner_id), officer_id) == sorted({str(owner_id), str(officer_id)})
        # unknown ids and types never raise; the actor is still included
        assert audience_for(db, "challan", str(uuid4()), officer_id) == [str(officer_id)]
        assert audience_for(db, "challan", "not-a-uuid", None) == []
    finally:
        db.rollback()
        db.close()


def test_citizen_stream_only_gets_what_concerns_them():
    """End to end through log_action and the hub, with real users."""
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.services.audit import log_action
    from app.services.event_visibility import viewer_for, visible_event
    from tests.conftest import create_user

    _, me_id = create_user("citizen")
    _, other_id = create_user("citizen")
    _, admin_id = create_user("admin")

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        hub.bind(loop)
        db = SessionLocal()
        try:
            me = viewer_for(db, db.get(User, me_id))
            q = hub.subscribe(lambda ev: visible_event(me, ev))
            log_action(db, "login", "session", str(uuid4()), "another citizen signs in", other_id)
            log_action(db, "update_user", "user", str(me_id), "admin edits my account", admin_id)
            log_action(db, "report_incident", "road_incident", str(uuid4()), "crash on Great East Rd", admin_id)
            log_action(db, "generate_challan", "challan", str(uuid4()), "a fine for somebody else", admin_id)
            log_action(db, "update_setting", "setting", "payments.currency", "= ZMW", admin_id)
        finally:
            db.rollback()
            db.close()
        loop.run_until_complete(asyncio.sleep(0.05))
        got = []
        while not q.empty():
            got.append(q.get_nowait())
        hub.unsubscribe(q)
        assert [(e["entity"], e["action"]) for e in got] == [("user", "update_user"),
                                                              ("road_incident", "report_incident")]
        assert all("actor_id" not in e and "audience" not in e for e in got)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception:
            pass


def test_open_stream_follows_session_and_permission_changes(client):
    from uuid import UUID

    from app.api.events import current_viewer
    from app.core.database import SessionLocal
    from app.core.security import decode_token
    from app.core.timeutil import utcnow
    from app.models.platform import UserSession
    from app.models.user import User

    token = _login(client)  # an admin
    claims = decode_token(token)
    user_id, sid = claims["sub"], UUID(claims["sid"])
    assert "users:manage" in current_viewer(user_id, sid).permissions

    # demoted while the stream is open: the next recheck narrows what they see
    db = SessionLocal()
    try:
        db.get(User, UUID(user_id)).role = UserRole.OFFICER
        db.commit()
    finally:
        db.close()
    demoted = current_viewer(user_id, sid)
    assert demoted is not None and demoted.is_staff and "users:manage" not in demoted.permissions

    # signed out: the stream must end
    db = SessionLocal()
    try:
        db.get(UserSession, sid).revoked_at = utcnow()
        db.commit()
    finally:
        db.close()
    assert current_viewer(user_id, sid) is None


def test_stream_ends_when_recheck_fails(monkeypatch):
    import app.api.events as events_api

    monkeypatch.setattr(events_api, "RECHECK_SECONDS", 0)
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        hub.bind(loop)
        q = hub.subscribe()

        async def denied() -> bool:
            return False

        async def collect():
            return [frame async for frame in events_api.source(q, denied)]

        frames = loop.run_until_complete(asyncio.wait_for(collect(), timeout=2))
        assert len(frames) == 1 and "hello" in frames[0]  # hello, then the stream closes
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception:
            pass