"""Requirement gaps closed after the first review: admin network restriction (R15),
PSV geo-fencing (R4), FASTag identification and free-flow tolling (R8)."""

import app.core.ratelimit as ratelimit
import pytest
from fastapi.testclient import TestClient

from app.services import settings as runtime_settings
from main import app
from tests.conftest import create_user

client = TestClient(app)
PW = "password123"


@pytest.fixture(autouse=True)
def _reset():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()
    runtime_settings.invalidate_cache()


def _headers(role="admin", ip="10.1.1.5"):
    email, _uid = create_user(role)
    r = client.post("/api/auth/login", json={"email": email, "password": PW}, headers={"X-Forwarded-For": ip})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Forwarded-For": ip}


# ---------------------------------------------------------------- admin network (R15)

def _set_allowlist(h, value):
    return client.put("/api/admin/settings/security.admin_ip_allowlist", json={"value": value}, headers=h)


def test_allowlist_helpers():
    assert runtime_settings.ip_allowed("", "8.8.8.8")
    assert runtime_settings.ip_allowed("10.0.0.0/8, 196.1.2.3", "10.9.9.9")
    assert runtime_settings.ip_allowed("10.0.0.0/8, 196.1.2.3", "196.1.2.3")
    assert not runtime_settings.ip_allowed("10.0.0.0/8", "11.0.0.1")
    assert not runtime_settings.ip_allowed("10.0.0.0/8", "not-an-ip")
    with pytest.raises(ValueError):
        runtime_settings.validate("security.admin_ip_allowlist", "10.0.0.0/8, banana")


def test_admin_blocked_outside_allowlist_but_others_unaffected():
    admin = _headers("admin", "10.1.1.5")
    officer = _headers("officer", "8.8.8.8")
    assert _set_allowlist(admin, "10.1.1.0/24").status_code == 200

    assert client.get("/api/auth/me", headers=admin).status_code == 200
    outside = {**admin, "X-Forwarded-For": "8.8.4.4"}
    blocked = client.get("/api/auth/me", headers=outside)
    assert blocked.status_code == 403
    assert "network" in blocked.json()["detail"].lower()
    # only administrators are restricted
    assert client.get("/api/auth/me", headers=officer).status_code == 200

    assert client.delete("/api/admin/settings/security.admin_ip_allowlist", headers=admin).status_code == 204
    assert client.get("/api/auth/me", headers=outside).status_code == 200


def test_cannot_save_an_allowlist_that_locks_you_out():
    admin = _headers("admin", "10.1.1.5")
    r = _set_allowlist(admin, "192.168.0.0/16")
    assert r.status_code == 400
    assert "locked out" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=admin).status_code == 200


def test_allowlist_rejects_garbage():
    admin = _headers("admin", "10.1.1.5")
    assert _set_allowlist(admin, "10.0.0.0/8, nope").status_code == 422


# ---------------------------------------------------------------- PSV geo-fencing (R4)

import json  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from uuid import uuid4  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.notification import NotificationRule  # noqa: E402
from app.models.psv import PSVOperator, PSVPermit  # noqa: E402
from app.models.vehicle import Vehicle  # noqa: E402
from app.services.geofence import distance_to_corridor_m  # noqa: E402

# A straight route running east along latitude -15.40 from longitude 28.30 to 28.40
ROUTE = [{"lat": -15.40, "lon": 28.30}, {"lat": -15.40, "lon": 28.40}]


def _vehicle(plate=None, **extra):
    db = SessionLocal()
    try:
        v = Vehicle(
            registration_number=plate or f"PSV{uuid4().hex[:5].upper()}", owner_name="Test Owner",
            owner_id_number="123456/10/1", make="Toyota", model="Coaster", year=2020, **extra,
        )
        db.add(v)
        db.commit()
        return str(v.id), v.registration_number
    finally:
        db.close()


def _permit(vehicle_id):
    db = SessionLocal()
    try:
        op = PSVOperator(name="Op", licence_number=f"L{uuid4().hex[:8]}", contact_person="A", phone="0970000000")
        db.add(op)
        db.flush()
        p = PSVPermit(
            operator_id=op.id, vehicle_id=vehicle_id, permit_number=f"P{uuid4().hex[:8]}", route="Lusaka-Kafue",
            issued_date=datetime.utcnow() - timedelta(days=5), expiry_date=datetime.utcnow() + timedelta(days=300),
        )
        db.add(p)
        db.commit()
        return str(p.id)
    finally:
        db.close()


def _ensure_rule(event="psv_route_violation"):
    db = SessionLocal()
    try:
        if not db.query(NotificationRule).filter(NotificationRule.trigger_event == event).first():
            db.add(NotificationRule(trigger_event=event, channels="in_app", title_template="Off route",
                                    body_template="{registration} {distance} m", is_active=True))
            db.commit()
    finally:
        db.close()


def test_distance_to_corridor():
    wp = [[-15.40, 28.30], [-15.40, 28.40]]
    assert distance_to_corridor_m(-15.40, 28.35, wp) < 1
    # 0.01 degrees of latitude is about 1,112 m
    assert 1100 < distance_to_corridor_m(-15.39, 28.35, wp) < 1125
    # beyond the end of the route the distance is to the end point, not the infinite line
    assert distance_to_corridor_m(-15.40, 28.41, wp) > 1000


def test_geofence_inside_outside_and_alert():
    officer = _headers("officer")
    vid, plate = _vehicle()
    pid = _permit(vid)
    _ensure_rule()

    # no corridor yet
    r = client.post("/api/psv/positions", json={"plate_number": plate, "latitude": -15.4, "longitude": 28.35}, headers=officer)
    assert r.status_code == 409

    r = client.put(f"/api/psv/permits/{pid}/corridor", json={"waypoints": ROUTE, "buffer_m": 500}, headers=officer)
    assert r.status_code == 200 and r.json()["buffer_m"] == 500

    ok = client.post("/api/psv/positions", json={"plate_number": plate, "latitude": -15.4005, "longitude": 28.35}, headers=officer)
    assert ok.status_code == 200 and ok.json()["inside"] is True and ok.json()["alert_id"] is None

    off = client.post("/api/psv/positions", json={"plate_number": plate, "latitude": -15.39, "longitude": 28.35}, headers=officer)
    body = off.json()
    assert body["inside"] is False and body["alert_id"] and body["distance_m"] > 1000

    # a second stray report inside the dedupe window does not raise another alert
    again = client.post("/api/psv/positions", json={"plate_number": plate, "latitude": -15.389, "longitude": 28.36}, headers=officer)
    assert again.json()["alert_id"] == body["alert_id"] and again.json()["alert_deduplicated"] is True

    alerts = client.get("/api/psv/alerts?acknowledged=false", headers=officer).json()
    assert any(a["id"] == body["alert_id"] for a in alerts)

    ack = client.post(f"/api/psv/alerts/{body['alert_id']}/acknowledge", headers=officer)
    assert ack.status_code == 200 and ack.json()["acknowledged"] is True
    # once acknowledged, a new stray position raises a fresh alert
    fresh = client.post("/api/psv/positions", json={"plate_number": plate, "latitude": -15.39, "longitude": 28.35}, headers=officer)
    assert fresh.json()["alert_id"] != body["alert_id"]


def test_geofence_rules_and_validation():
    officer = _headers("officer")
    citizen = _headers("citizen")
    vid, plate = _vehicle()
    pid = _permit(vid)
    # only officers may define corridors; citizens may not report positions
    assert client.put(f"/api/psv/permits/{pid}/corridor", json={"waypoints": ROUTE}, headers=citizen).status_code == 403
    assert client.post("/api/psv/positions", json={"plate_number": plate, "latitude": 0, "longitude": 0}, headers=citizen).status_code == 403
    # bad input
    assert client.put(f"/api/psv/permits/{pid}/corridor", json={"waypoints": ROUTE[:1]}, headers=officer).status_code == 422
    assert client.put(f"/api/psv/permits/{pid}/corridor", json={"waypoints": ROUTE, "buffer_m": 5}, headers=officer).status_code == 422
    assert client.post("/api/psv/positions", json={"plate_number": plate, "latitude": 95, "longitude": 0}, headers=officer).status_code == 422
    # unknown vehicle, and a vehicle with no permit
    assert client.post("/api/psv/positions", json={"plate_number": "NOPE123", "latitude": 0, "longitude": 0}, headers=officer).status_code == 404
    _vid2, plate2 = _vehicle()
    assert client.post("/api/psv/positions", json={"plate_number": plate2, "latitude": 0, "longitude": 0}, headers=officer).status_code == 404


# ---------------------------------------------------------------- FASTag and free-flow tolling (R8)

from app.core.database import SessionLocal as _S  # noqa: E402
from app.models.insurance import Insurance  # noqa: E402
from app.models.inspection import FitnessCertificate  # noqa: E402
from app.models.toll import TollTransaction  # noqa: E402


def _compliant_vehicle(tag=None):
    """A vehicle that passes every compliance check."""
    from app.models.inspection import Inspection, InspectionResult

    vid, plate = _vehicle(fastag_id=tag)
    db = _S()
    try:
        now = datetime.utcnow()
        db.add(Insurance(vehicle_id=vid, provider="Test Insurer", policy_number=f"POL{uuid4().hex[:8]}",
                         start_date=now - timedelta(days=10), end_date=now + timedelta(days=300), is_active=True))
        insp = Inspection(vehicle_id=vid, inspection_centre="Lusaka", scheduled_date=now - timedelta(days=10),
                          result=list(InspectionResult)[0])
        db.add(insp)
        db.flush()
        db.add(FitnessCertificate(inspection_id=insp.id, vehicle_id=vid, certificate_number=f"FC{uuid4().hex[:8]}",
                                  issued_date=now - timedelta(days=10), expiry_date=now + timedelta(days=300)))
        db.commit()
    finally:
        db.close()
    return vid, plate


def test_fastag_identification_and_mismatch():
    op = _headers("toll_operator")
    tag = f"TAG{uuid4().hex[:8].upper()}"
    _vid, plate = _compliant_vehicle(tag)
    ok = client.post("/api/toll/events", json={"tag_id": tag, "gate_id": "G1", "toll_amount": 5000}, headers=op)
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["compliance_result"] == "compliant" and body["plate_number"] == plate
    assert body["transaction"]["identification_method"] == "fastag" and body["action"] == "proceed"

    # unknown tag
    assert client.post("/api/toll/events", json={"tag_id": "NOSUCHTAG1", "gate_id": "G1"}, headers=op).status_code == 404
    # neither identifier
    assert client.post("/api/toll/events", json={"gate_id": "G1"}, headers=op).status_code == 422
    # plate that disagrees with the tag: flagged, held at the barrier, and not fined
    other_plate = _vehicle()[1]
    bad = client.post("/api/toll/events", json={"tag_id": tag, "plate_number": other_plate, "gate_id": "G1"}, headers=op).json()
    assert bad["compliance_result"] == "flagged"
    assert any("mismatch" in i for i in bad["flagged_issues"])
    assert bad["action"] == "hold_at_barrier" and bad["barrier_required"] is True and bad["challan_created"] is False


def test_plate_event_still_works_and_records_method():
    op = _headers("toll_operator")
    _vid, plate = _compliant_vehicle()
    r = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "G2", "toll_amount": 5000}, headers=op).json()
    assert r["transaction"]["identification_method"] == "anpr" and r["transaction"]["lane_mode"] == "barrier"
    assert r["transaction"]["is_paid"] is True


def test_free_flow_bills_owner_and_never_holds_a_barrier():
    op = _headers("toll_operator")
    _vid, plate = _compliant_vehicle()
    r = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "M1", "toll_amount": 7000, "lane_mode": "mlff"}, headers=op).json()
    assert r["compliance_result"] == "compliant" and r["action"] == "proceed"
    assert r["transaction"]["lane_mode"] == "mlff" and r["transaction"]["is_paid"] is False  # billed, not paid

    # a non-compliant vehicle in a free-flow lane is dispatched to, never stopped by a barrier
    _v2, bad_plate = _vehicle()  # no insurance or fitness
    bad = client.post("/api/toll/events", json={"plate_number": bad_plate, "gate_id": "M1", "lane_mode": "mlff"}, headers=op).json()
    assert bad["compliance_result"] == "flagged"
    assert bad["action"] == "dispatch_enforcement" and bad["barrier_required"] is False


def test_unpaid_free_flow_toll_becomes_a_toll_due_after_grace():
    op = _headers("toll_operator")
    vid, plate = _compliant_vehicle()
    first = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "M1", "toll_amount": 7000, "lane_mode": "mlff"}, headers=op).json()
    # inside the grace period the earlier unpaid toll does not flag the vehicle
    again = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "M2", "toll_amount": 7000, "lane_mode": "mlff"}, headers=op).json()
    assert again["compliance_result"] == "compliant"

    # age both passages past the grace period
    db = _S()
    try:
        for t in db.query(TollTransaction).filter(TollTransaction.plate_number == plate).all():
            t.timestamp = datetime.utcnow() - timedelta(days=30)
        db.commit()
    finally:
        db.close()
    flagged = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "M3", "lane_mode": "mlff"}, headers=op).json()
    assert flagged["compliance_result"] == "flagged"
    assert any("toll due" in i for i in flagged["flagged_issues"])
    assert flagged["challan_created"] is False  # an unpaid toll is a reason to chase payment, not a new fine

    # settling the dues clears the flag
    for tid in [first["transaction"]["id"], again["transaction"]["id"]]:
        assert client.post(f"/api/toll/transactions/{tid}/settle", headers=op).status_code == 200
    assert client.post(f"/api/toll/transactions/{first['transaction']['id']}/settle", headers=op).status_code == 400
    cleared = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "M4", "lane_mode": "mlff"}, headers=op).json()
    assert cleared["compliance_result"] == "compliant"
