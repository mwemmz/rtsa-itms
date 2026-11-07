"""Pay-for services, insurance policy types, the inspection checklist, public certificate check and insights."""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

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

from app.models.psv import PSVOperator, PSVPermit  # noqa: E402
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


def _vehicle(officer, owner_uid):
    v = client.post("/api/vehicles/", headers=officer, json={
        "registration_number": f"SV {uuid4().hex[:4].upper()}", "owner_name": "Owner", "owner_id_number": uuid4().hex[:9],
        "make": "Toyota", "model": "Hiace", "year": 2016})
    assert v.status_code == 201, v.text
    db = SessionLocal()
    try:
        from app.models.vehicle import Vehicle
        row = db.get(Vehicle, v.json()["id"])
        row.user_id = owner_uid
        db.commit()
    finally:
        db.close()
    return v.json()["id"]


def test_fitness_test_is_paid_for_and_booked_once():
    officer, _, _ = _login("officer")
    h, uid, _ = _login("citizen")
    vid = _vehicle(officer, uid)
    cat = client.get("/api/services/catalogue", headers=h).json()
    assert {"fitness_test", "psv_permit_renewal", "licence_renewal", "duplicate_licence"} <= {s["id"] for s in cat["services"]}
    assert any(n["name"] == "Road tax" for n in cat["not_handled_here"])
    r = client.post("/api/services/pay", headers=h, json={"service": "fitness_test", "vehicle_id": vid})
    assert r.status_code == 200, r.text
    assert r.json()["receipt_number"] and r.json()["amount"] == 15000
    insp = client.get(f"/api/inspections/vehicle/{vid}", headers=officer).json()
    assert len(insp) == 1 and insp[0]["result"] == "pending"
    again = client.post("/api/services/pay", headers=h, json={"service": "fitness_test", "vehicle_id": vid})
    assert again.status_code == 409


def test_cannot_pay_for_someone_elses_vehicle():
    officer, _, _ = _login("officer")
    _, owner_uid, _ = _login("citizen")
    other, _, _ = _login("citizen")
    vid = _vehicle(officer, owner_uid)
    r = client.post("/api/services/pay", headers=other, json={"service": "fitness_test", "vehicle_id": vid})
    assert r.status_code == 404


def test_psv_permit_renewal_extends_expiry():
    officer, _, _ = _login("officer")
    h, uid, _ = _login("citizen")
    vid = _vehicle(officer, uid)
    assert client.post("/api/services/pay", headers=h, json={"service": "psv_permit_renewal", "vehicle_id": vid}).status_code == 404
    db = SessionLocal()
    try:
        op = PSVOperator(name=f"Op {uuid4().hex[:5]}", licence_number=f"OP-{uuid4().hex[:6]}", contact_person="A B", phone="0977000000")
        db.add(op); db.flush()
        old = datetime.utcnow() + timedelta(days=10)
        p = PSVPermit(operator_id=op.id, vehicle_id=vid, permit_number=f"PSV-{uuid4().hex[:6]}", route="Lusaka-Kabwe",
                      issued_date=datetime.utcnow() - timedelta(days=355), expiry_date=old)
        db.add(p); db.commit(); pid = p.id
    finally:
        db.close()
    # without passenger-liability cover the renewal is refused
    blocked = client.post("/api/services/pay", headers=h, json={"service": "psv_permit_renewal", "vehicle_id": vid})
    assert blocked.status_code == 409 and "passenger-liability" in blocked.json()["detail"]
    assert client.post("/api/insurance/", headers=officer, json={
        "vehicle_id": vid, "provider": "ZSIC", "policy_number": f"P-{uuid4().hex[:6]}", "policy_type": "psv_passenger",
        "start_date": (datetime.utcnow() - timedelta(days=30)).isoformat(), "end_date": (datetime.utcnow() + timedelta(days=300)).isoformat(),
    }).status_code == 201
    r = client.post("/api/services/pay", headers=h, json={"service": "psv_permit_renewal", "vehicle_id": vid})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        assert db.get(PSVPermit, pid).expiry_date > old + timedelta(days=360)
    finally:
        db.close()


def test_policy_types_listed_and_validated():
    officer, _, _ = _login("officer")
    h, uid, _ = _login("citizen")
    vid = _vehicle(officer, uid)
    types = client.get("/api/insurance/policy-types", headers=officer).json()
    assert {"third_party", "comprehensive", "psv_passenger"} <= {t["id"] for t in types}
    assert all(t["covers"] for t in types)
    body = {"vehicle_id": vid, "provider": "ZSIC", "policy_number": f"P-{uuid4().hex[:6]}", "policy_type": "comprehensive",
            "premium": 250000, "start_date": "2026-01-01T00:00:00", "end_date": "2027-01-01T00:00:00"}
    ok = client.post("/api/insurance/", headers=officer, json=body)
    assert ok.status_code == 201 and ok.json()["policy_type"] == "comprehensive"
    bad = client.post("/api/insurance/", headers=officer, json={**body, "policy_number": f"P-{uuid4().hex[:6]}", "policy_type": "magic"})
    assert bad.status_code == 422


def _scheduled(officer, vid):
    return client.post("/api/inspections/", headers=officer, json={
        "vehicle_id": vid, "inspection_centre": "Lusaka Main VID Centre",
        "scheduled_date": (datetime.utcnow() + timedelta(days=1)).isoformat()}).json()["id"]


def test_checklist_decides_the_result_and_certificate_verifies_publicly():
    officer, _, _ = _login("officer")
    _, uid, _ = _login("citizen")
    vid = _vehicle(officer, uid)
    spec = client.get("/api/inspections/checklist-spec", headers=officer).json()
    items = [c["id"] for c in spec["checklist"]]
    iid = _scheduled(officer, vid)
    fails = {k: "pass" for k in items} | {"brakes": "fail"}
    # asking to pass a vehicle whose brakes failed is refused
    assert client.patch(f"/api/inspections/{iid}", headers=officer, json={"result": "passed", "checklist": fails}).status_code == 409
    r = client.patch(f"/api/inspections/{iid}", headers=officer, json={"checklist": fails, "readings": {"brake_efficiency_pct": 38}})
    assert r.status_code == 200 and r.json()["result"] == "failed" and "Brakes failed" in r.json()["findings"]
    assert client.get(f"/api/inspections/{iid}/fitness-certificate", headers=officer).status_code == 404
    iid2 = _scheduled(officer, vid)
    good = {k: "pass" for k in items}
    # partial checklist cannot pass
    assert client.patch(f"/api/inspections/{iid2}", headers=officer, json={"checklist": {"brakes": "pass"}}).status_code == 400
    r = client.patch(f"/api/inspections/{iid2}", headers=officer, json={"checklist": good, "readings": {"brake_efficiency_pct": 71, "tyre_tread_mm": 4.2}})
    assert r.json()["result"] == "passed" and r.json()["readings"]["tyre_tread_mm"] == 4.2
    cert = client.get(f"/api/inspections/{iid2}/fitness-certificate", headers=officer).json()
    pub = client.get(f"/api/verify/fitness/{cert['certificate_number']}")  # no sign-in
    assert pub.status_code == 200 and pub.json()["valid"] is True and pub.json()["plate"]
    assert client.get("/api/verify/fitness/NOPE").status_code == 404
    qr = client.get(f"/api/verify/fitness/{cert['certificate_number']}/qr.svg")
    assert qr.status_code == 200 and b"<svg" in qr.content


def test_insights_are_for_staff_only_and_explain_scores():
    h, _, _ = _login("citizen")
    assert client.get("/api/insights/", headers=h).status_code == 403
    admin, _, _ = _login("admin")
    d = client.get("/api/insights/", headers=admin).json()
    assert "risk_rules" in d and set(d["expiry_pipeline"]) == {"Insurance", "Fitness certificates", "Driving licences", "PSV permits"}
    for r in d["risky_vehicles"]:
        assert r["why"]


def test_obd_fault_codes_are_decoded_and_blocking_codes_fail_the_test():
    officer, _, _ = _login("officer")
    _, uid, _ = _login("citizen")
    vid = _vehicle(officer, uid)
    items = [c["id"] for c in client.get("/api/inspections/checklist-spec", headers=officer).json()["checklist"]]
    dec = client.post("/api/inspections/obd/decode", headers=officer, json={"codes": ["p0420", "P0128", "P0420"]}).json()
    assert [d["code"] for d in dec] == ["P0420", "P0128"]
    assert dec[0]["blocking"] is True and dec[1]["blocking"] is False and "Catalytic" in dec[0]["meaning"]
    assert client.post("/api/inspections/obd/decode", headers=officer, json={"codes": ["XYZ"]}).status_code == 400
    good = {k: "pass" for k in items}
    iid = _scheduled(officer, vid)
    # a misfire code fails an otherwise clean vehicle
    bad = client.patch(f"/api/inspections/{iid}", headers=officer, json={"checklist": good, "obd_codes": ["P0301"]}).json()
    assert bad["result"] == "failed" and "P0301" in bad["findings"] and bad["obd_codes"] == ["P0301"]
    # an advisory code alone does not
    iid2 = _scheduled(officer, vid)
    ok = client.patch(f"/api/inspections/{iid2}", headers=officer, json={"checklist": good, "obd_codes": ["P0128"]}).json()
    assert ok["result"] == "passed"
