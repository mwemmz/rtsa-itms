"""Appeals against fines, demerit points that suspend a licence, and stolen vehicles that must not cost the owner."""

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

from app.models.driver import Driver, DriverStatus, LicenceClass  # noqa: E402
from app.models.enforcement import Challan, ChallanStatus  # noqa: E402
from app.services.overdue import mark_overdue_challans  # noqa: E402
from main import app  # noqa: E402
from tests.conftest import create_user  # noqa: E402

client = TestClient(app)
LONG = "The photo shows a different car, mine was parked at home."


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


def _driver(user_id, email):
    nid = uuid4().hex[:9]
    db = SessionLocal()
    try:
        d = Driver(licence_number=f"AD-{uuid4().hex[:8].upper()}", first_name="Chola", last_name="Mwape", id_number=nid,
                   date_of_birth=datetime(1990, 1, 1), licence_class=LicenceClass.B,
                   licence_issue_date=datetime.utcnow() - timedelta(days=300),
                   licence_expiry_date=datetime.utcnow() + timedelta(days=300), user_id=user_id, email=email)
        db.add(d)
        db.commit()
        return str(d.id), nid
    finally:
        db.close()


def _world():
    """An officer, an owner who is also a licensed driver, their vehicle."""
    officer, _, _ = _login("officer")
    owner, uid, email = _login("citizen")
    driver_id, nid = _driver(uid, email)
    v = client.post("/api/vehicles/", headers=officer, json={
        "registration_number": f"AD {uuid4().hex[:4].upper()}", "owner_name": "Owner", "owner_id_number": nid,
        "make": "Toyota", "model": "Vitz", "year": 2015})
    assert v.status_code in (200, 201), v.text
    return officer, owner, v.json()["id"], v.json()["registration_number"], driver_id


def _fine(officer, vehicle_id, driver_id=None, offence="expired_fitness"):
    body = {"vehicle_id": vehicle_id, "violation_type": offence, "location": "Kafue Road"}
    if driver_id:
        body["driver_id"] = driver_id
    r = client.post("/api/enforcement/violations", headers=officer, json=body)
    assert r.status_code == 201, r.text


def _fines(h, paid=True):
    return client.get(f"/api/portal/fines?include_paid={'true' if paid else 'false'}", headers=h).json()


# ---------------------------------------------------------------- appeals

def test_appeal_disputes_the_fine_and_officer_can_uphold_it():
    officer, owner, vid, _, _ = _world()
    _fine(officer, vid)
    f = _fines(owner)[0]
    assert f["can_appeal"] is True
    assert client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "wrong_vehicle", "details": "short"}).status_code == 422
    r = client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "wrong_vehicle", "details": LONG})
    assert r.status_code == 201 and r.json()["status"] == "pending"
    assert _fines(owner)[0]["status"] == "disputed" and _fines(owner)[0]["can_appeal"] is False
    assert client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "other", "details": LONG}).status_code == 409

    pending = client.get("/api/enforcement/appeals", headers=officer).json()
    mine = next(a for a in pending if a["reference"] == f["reference"])
    assert client.post(f"/api/enforcement/appeals/{mine['id']}/decide", headers=owner, json={"decision": "upheld"}).status_code == 403
    assert client.post(f"/api/enforcement/appeals/{mine['id']}/decide", headers=officer, json={"decision": "upheld", "note": "Plate misread"}).status_code == 200
    assert _fines(owner)[0]["status"] == "cancelled"
    assert _fines(owner, paid=False) == []  # nothing left to pay
    assert client.post(f"/api/portal/fines/{f['id']}/pay", headers=owner).status_code == 409


def test_dismissed_appeal_restores_the_fine_with_a_week_to_pay_and_cannot_be_repeated():
    officer, owner, vid, _, _ = _world()
    _fine(officer, vid)
    f = _fines(owner)[0]
    client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "other", "details": LONG})
    # a disputed fine does not go overdue while it is being looked at
    db = SessionLocal()
    try:
        c = db.get(Challan, UUID(f["id"]))
        c.due_date = datetime.utcnow() - timedelta(days=2)
        db.commit()
        mark_overdue_challans(db)
        db.commit()
        assert db.get(Challan, UUID(f["id"])).status == ChallanStatus.DISPUTED
    finally:
        db.close()
    a = next(x for x in client.get("/api/enforcement/appeals", headers=officer).json() if x["reference"] == f["reference"])
    assert client.post(f"/api/enforcement/appeals/{a['id']}/decide", headers=officer, json={"decision": "dismissed"}).status_code == 200
    after = _fines(owner)[0]
    assert after["status"] == "unpaid" and after["can_appeal"] is False
    assert datetime.fromisoformat(after["due_date"]) > datetime.utcnow() + timedelta(days=6)
    assert client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "other", "details": LONG}).status_code == 409


def test_only_the_person_who_owes_the_fine_can_appeal_and_paying_ends_the_appeal():
    officer, owner, vid, _, _ = _world()
    stranger, _, _ = _login("citizen")
    _fine(officer, vid)
    f = _fines(owner)[0]
    assert client.post(f"/api/portal/fines/{f['id']}/appeal", headers=stranger, json={"reason": "other", "details": LONG}).status_code == 404
    client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "other", "details": LONG})
    assert client.post(f"/api/portal/fines/{f['id']}/pay", headers=owner).status_code == 200
    assert client.get("/api/enforcement/appeals?status=withdrawn", headers=officer).json()


def test_an_old_fine_can_still_be_appealed():
    officer, owner, vid, _, _ = _world()
    _fine(officer, vid)
    f = _fines(owner)[0]
    db = SessionLocal()
    try:
        c = db.get(Challan, UUID(f["id"]))
        c.created_at = datetime.utcnow() - timedelta(days=40)
        db.commit()
    finally:
        db.close()
    assert _fines(owner)[0]["can_appeal"] is True
    assert client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "other", "details": LONG}).status_code == 201


# ---------------------------------------------------------------- demerit points

def _points(h):
    return client.get("/api/portal/licence", headers=h).json()


def test_driver_offences_add_points_and_reaching_the_limit_suspends_the_licence():
    officer, owner, vid, _, driver_id = _world()
    assert _points(owner)["demerit_points"] == 0
    _fine(officer, vid, driver_id, "using_phone")  # 3
    assert _points(owner)["demerit_points"] == 3 and _points(owner)["demerit_limit"] == 12
    _fine(officer, vid, driver_id, "reckless_driving")  # 5 -> 8
    _fine(officer, vid, driver_id, "rear_seat_belt")  # 2 -> 10
    assert _points(owner)["status"] == "active"
    _fine(officer, vid, driver_id, "using_phone")  # 3 -> 13
    lic = _points(owner)
    assert lic["demerit_points"] == 13 and lic["status"] == "suspended" and lic["can_renew"] is False


def test_vehicle_offences_carry_no_points_and_a_cancelled_fine_gives_its_points_back():
    officer, owner, vid, _, driver_id = _world()
    _fine(officer, vid, driver_id, "expired_fitness")
    assert _points(owner)["demerit_points"] == 0
    _fine(officer, vid, driver_id, "running_red_light")  # 4
    assert _points(owner)["demerit_points"] == 4
    f = next(x for x in _fines(owner) if x["violation_type"] == "running_red_light")
    client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "not_me", "details": LONG})
    a = next(x for x in client.get("/api/enforcement/appeals", headers=officer).json() if x["reference"] == f["reference"])
    client.post(f"/api/enforcement/appeals/{a['id']}/decide", headers=officer, json={"decision": "upheld"})
    assert _points(owner)["demerit_points"] == 0


# ---------------------------------------------------------------- stolen vehicles

def test_reporting_a_theft_waives_later_fines_and_asks_the_owner_nothing():
    officer, owner, vid, plate, _ = _world()
    stranger, _, _ = _login("citizen")
    assert client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=stranger, json={"police_report_number": "CR-1/26"}).status_code == 404
    future = (datetime.utcnow() + timedelta(days=1)).isoformat()
    assert client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={"police_report_number": "CR-1/26", "stolen_at": future}).status_code == 422
    r = client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={"police_report_number": "CR-1/26"})
    assert r.status_code == 200 and r.json()["status"] == "stolen"
    assert client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={"police_report_number": "CR-1/26"}).status_code == 409

    _fine(officer, vid, None, "using_phone")  # an offence by whoever has the car
    fines = _fines(owner)
    assert [f["status"] for f in fines] == ["cancelled"] and fines[0]["nomination_status"] == "none"
    assert _fines(owner, paid=False) == []
    assert client.get("/api/portal/nominations", headers=owner).json() == []


def test_toll_gate_sighting_of_a_stolen_vehicle_fines_nobody():
    officer, owner, vid, plate, _ = _world()
    toll, _, _ = _login("toll_operator")
    client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={"police_report_number": "CR-2/26"})
    r = client.post("/api/toll/events", headers=toll, json={"plate_number": plate, "gate_id": "KAFUE-N"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["compliance_result"] == "flagged" and body["challan_created"] is False
    assert any("Reported stolen" in i for i in body["flagged_issues"])
    assert _fines(owner) == []


def test_recovered_vehicle_is_fined_normally_again():
    officer, owner, vid, plate, _ = _world()
    client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={"police_report_number": "CR-3/26"})
    assert client.post(f"/api/enforcement/vehicles/{vid}/recovered", headers=owner).status_code == 403
    assert client.post(f"/api/enforcement/vehicles/{vid}/recovered", headers=officer).json()["status"] == "active"
    _fine(officer, vid, None, "expired_fitness")
    assert [f["status"] for f in _fines(owner)] == ["unpaid"]


def test_owner_can_undo_a_stolen_report_from_their_vehicles_page():
    officer, owner, vid, _, _ = _world()
    stranger, _, _ = _login("citizen")
    client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={"police_report_number": "CR-4/26"})
    assert client.post(f"/api/portal/vehicles/{vid}/recovered", headers=stranger).status_code == 404
    r = client.post(f"/api/portal/vehicles/{vid}/recovered", headers=owner)
    assert r.status_code == 200 and r.json()["status"] == "active"
    # It is no longer marked stolen, so it cannot be "recovered" again.
    assert client.post(f"/api/portal/vehicles/{vid}/recovered", headers=owner).status_code == 404
    _fine(officer, vid, None, "expired_fitness")
    assert [f["status"] for f in _fines(owner)] == ["unpaid"]


def test_theft_reference_is_issued_by_the_system():
    _, owner, vid, _, _ = _world()
    r = client.post(f"/api/portal/vehicles/{vid}/report-stolen", headers=owner, json={})
    assert r.status_code == 200 and r.json()["police_report_number"].startswith("THEFT-")


def test_appeal_can_carry_photos_the_officer_can_see():
    officer, owner, vid, plate, driver_id = _world()
    _fine(officer, vid, driver_id, "using_phone")
    f = _fines(owner)[0]
    a = client.post(f"/api/portal/fines/{f['id']}/appeal", headers=owner, json={"reason": "not_me", "details": LONG}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    ok = client.post(f"/api/portal/appeals/{a['id']}/photos", headers=owner, files={"file": ("p.png", png, "image/png")})
    assert ok.status_code == 201, ok.text
    bad = client.post(f"/api/portal/appeals/{a['id']}/photos", headers=owner, files={"file": ("p.txt", b"hello", "text/plain")})
    assert bad.status_code == 415
    stranger, _, _ = _login("citizen")
    assert client.post(f"/api/portal/appeals/{a['id']}/photos", headers=stranger, files={"file": ("p.png", png, "image/png")}).status_code == 404
    listed = [x for x in client.get("/api/enforcement/appeals", headers=officer).json() if x["id"] == a["id"]][0]
    assert listed["photos"] == [ok.json()["id"]]
    pid = ok.json()["id"]
    assert client.get(f"/api/portal/appeals/{a['id']}/photos/{pid}", headers=officer).status_code == 200
    assert client.get(f"/api/portal/appeals/{a['id']}/photos/{pid}", headers=owner).status_code == 200
    assert client.get(f"/api/portal/appeals/{a['id']}/photos/{pid}", headers=stranger).status_code == 404
