"""The owner is asked who was driving; the named driver accepts or rejects; an officer settles disputes."""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TRUST_PROXY_HEADERS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402
import app.models  # noqa: F401, E402
import app.core.ratelimit as ratelimit  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402

Base.metadata.create_all(bind=engine)

from app.models.driver import Driver, LicenceClass  # noqa: E402
from app.models.enforcement import Challan  # noqa: E402
from app.services.nomination import expire_nominations  # noqa: E402
from main import app  # noqa: E402
from tests.conftest import create_user  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _login(role, **extra):
    email, uid = create_user(role, **extra)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, uid, email


def _driver(user_id=None, email=None):
    licence = f"NM-{uuid4().hex[:8].upper()}"
    nid = uuid4().hex[:9]
    db = SessionLocal()
    try:
        db.add(Driver(licence_number=licence, first_name="Mutale", last_name="Banda", id_number=nid,
                      date_of_birth=datetime(1990, 1, 1), licence_class=LicenceClass.B,
                      licence_issue_date=datetime.utcnow() - timedelta(days=300),
                      licence_expiry_date=datetime.utcnow() + timedelta(days=300), user_id=user_id, email=email))
        db.commit()
    finally:
        db.close()
    return licence, nid


def _setup(offence="using_phone"):
    """An owner (with an account and licence), a vehicle registered to them, and a fine with no driver on record."""
    officer, _, _ = _login("officer")
    owner_h, owner_id, owner_email = _login("citizen")
    _, owner_nid = _driver(owner_id, owner_email)
    plate = f"NM {uuid4().hex[:4].upper()}"
    v = client.post("/api/vehicles/", headers=officer, json={
        "registration_number": plate, "owner_name": "Owner", "owner_id_number": owner_nid,
        "make": "Toyota", "model": "Corolla", "year": 2016})
    assert v.status_code in (200, 201), v.text
    r = client.post("/api/enforcement/violations", headers=officer, json={
        "vehicle_id": v.json()["id"], "violation_type": offence, "location": "Great East Road"})
    assert r.status_code == 201, r.text
    fines = client.get("/api/portal/fines", headers=owner_h).json()
    assert len(fines) == 1
    return officer, owner_h, fines[0]["id"]


def _named_driver():
    h, uid, email = _login("citizen")
    licence, _ = _driver(uid, email)
    return h, licence


def test_owner_is_asked_who_was_driving_for_a_driver_offence():
    _, owner_h, cid = _setup("using_phone")
    asked = client.get("/api/portal/nominations", headers=owner_h).json()
    assert [a["id"] for a in asked] == [cid] and asked[0]["role"] == "owner"
    assert client.get("/api/portal/fines", headers=owner_h).json()[0]["nomination_status"] == "requested"


def test_a_vehicle_offence_stays_with_the_owner_and_asks_nothing():
    _, owner_h, _ = _setup("expired_fitness")
    assert client.get("/api/portal/nominations", headers=owner_h).json() == []


def test_named_driver_accepts_and_the_fine_moves_to_them():
    _, owner_h, cid = _setup()
    driver_h, licence = _named_driver()
    r = client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": licence.lower(), "statement": "My brother"})
    assert r.status_code == 200 and r.json()["status"] == "nominated"

    named = client.get("/api/portal/nominations", headers=driver_h).json()
    assert named[0]["role"] == "driver" and named[0]["id"] == cid
    assert client.get("/api/portal/nominations", headers=owner_h).json() == []  # answered

    r = client.post(f"/api/portal/nominations/{cid}/respond", headers=driver_h, json={"accept": True})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert cid in [f["id"] for f in client.get("/api/portal/fines", headers=driver_h).json()]
    assert cid not in [f["id"] for f in client.get("/api/portal/fines", headers=owner_h).json()]


def test_rejection_goes_to_an_officer_who_decides():
    officer, owner_h, cid = _setup()
    driver_h, licence = _named_driver()
    client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": licence})
    r = client.post(f"/api/portal/nominations/{cid}/respond", headers=driver_h, json={"accept": False, "note": "I was elsewhere"})
    assert r.json()["status"] == "rejected"
    # the owner still owes it while it is disputed
    assert cid in [f["id"] for f in client.get("/api/portal/fines", headers=owner_h).json()]

    pending = client.get("/api/enforcement/nominations/rejected", headers=officer).json()
    assert cid in [p["id"] for p in pending]
    assert client.post(f"/api/enforcement/challans/{cid}/nomination/resolve", headers=driver_h, json={"decision": "owner"}).status_code == 403
    r = client.post(f"/api/enforcement/challans/{cid}/nomination/resolve", headers=officer, json={"decision": "driver"})
    assert r.status_code == 200 and r.json()["nomination_status"] == "accepted"
    assert cid in [f["id"] for f in client.get("/api/portal/fines", headers=driver_h).json()]


def test_owner_naming_themselves_needs_no_second_step():
    _, owner_h, cid = _setup()
    me = client.get("/api/auth/me", headers=owner_h).json()["email"]
    db = SessionLocal()
    try:
        own = db.query(Driver).filter(Driver.email == me).first().licence_number
    finally:
        db.close()
    r = client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": own})
    assert r.status_code == 200 and r.json()["status"] == "accepted"


def test_guards():
    _, owner_h, cid = _setup()
    other_h, _, _ = _login("citizen")
    driver_h, licence = _named_driver()
    assert client.post(f"/api/portal/fines/{cid}/nominate", headers=other_h, json={"licence_number": licence}).status_code == 404
    assert client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": "NOPE-0000"}).status_code == 404
    # only the named driver may answer, and only once something is waiting
    assert client.post(f"/api/portal/nominations/{cid}/respond", headers=driver_h, json={"accept": True}).status_code == 404
    client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": licence})
    assert client.post(f"/api/portal/nominations/{cid}/respond", headers=other_h, json={"accept": True}).status_code == 404
    assert client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": licence}).status_code == 409


def test_unanswered_request_lapses_and_the_owner_stays_liable():
    _, owner_h, cid = _setup()
    db = SessionLocal()
    try:
        c = db.get(Challan, UUID(cid))
        c.nomination_deadline = datetime.utcnow() - timedelta(days=1)
        db.commit()
        assert expire_nominations(db) >= 1
        db.commit()
    finally:
        db.close()
    driver_h, licence = _named_driver()
    assert client.get("/api/portal/nominations", headers=owner_h).json() == []
    assert client.post(f"/api/portal/fines/{cid}/nominate", headers=owner_h, json={"licence_number": licence}).status_code == 409
    assert cid in [f["id"] for f in client.get("/api/portal/fines", headers=owner_h).json()]
