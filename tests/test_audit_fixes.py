"""Regression tests for the cross-role audit: access control on operational
endpoints, owner resolution for notifications, toll-gate fine duplication,
the licence workflow, conflict handling, token redaction and the Neon repair
migration."""

import logging
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as ratelimit
from app.core.database import SessionLocal
from app.models.driver import Driver
from app.models.enforcement import Challan, Violation, ViolationType
from app.models.inspection import FitnessCertificate, Inspection
from app.models.insurance import Insurance
from app.models.notification import Notification, NotificationRule
from app.models.vehicle import Vehicle
from main import app
from tests.conftest import create_user

client = TestClient(app)
PW = "password123"


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _headers(role, **extra):
    email, uid = create_user(role, **extra)
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, uid


def _rule(event):
    db = SessionLocal()
    try:
        if not db.query(NotificationRule).filter(NotificationRule.trigger_event == event).first():
            db.add(NotificationRule(trigger_event=event, channels="in_app", title_template=event, body_template=event))
            db.commit()
    finally:
        db.close()


def _vehicle(nrc="111111/11/1", owner_name="Owner Person", insured=False, fit=False, **extra):
    db = SessionLocal()
    try:
        v = Vehicle(registration_number=f"AU {uuid4().hex[:5].upper()}", owner_name=owner_name, owner_id_number=nrc,
                    make="Toyota", model="Corolla", year=2019, **extra)
        db.add(v)
        db.flush()
        now = datetime.utcnow()
        if insured:
            db.add(Insurance(vehicle_id=v.id, provider="ZSIC", policy_number=f"P-{uuid4().hex[:8]}",
                             start_date=now - timedelta(days=10), end_date=now + timedelta(days=300)))
        if fit:
            ins = Inspection(vehicle_id=v.id, inspection_centre="Lusaka", scheduled_date=now, result="PASSED")
            db.add(ins)
            db.flush()
            db.add(FitnessCertificate(inspection_id=ins.id, vehicle_id=v.id, certificate_number=f"F-{uuid4().hex[:8]}",
                                      issued_date=now, expiry_date=now + timedelta(days=300)))
        db.commit()
        return v.id, v.registration_number
    finally:
        db.close()


def _link_driver(user_id, nrc):
    db = SessionLocal()
    try:
        db.add(Driver(licence_number=f"L-{uuid4().hex[:8]}", first_name="A", last_name="B", id_number=nrc,
                      date_of_birth=datetime(1990, 1, 1), licence_class="B", licence_issue_date=datetime.utcnow(),
                      licence_expiry_date=datetime.utcnow() + timedelta(days=900), user_id=user_id))
        db.commit()
    finally:
        db.close()


def _notifications(user_id, event):
    db = SessionLocal()
    try:
        return db.query(Notification).filter(Notification.user_id == user_id, Notification.trigger_event == event).count()
    finally:
        db.close()


def _challans(vehicle_id):
    db = SessionLocal()
    try:
        return db.query(Challan).filter(Challan.vehicle_id == vehicle_id).count()
    finally:
        db.close()


# ============================ access control ============================

CITIZEN_FORBIDDEN = [
    ("get", "/api/vehicles/"),
    ("post", "/api/vehicles/"),
    ("get", "/api/drivers/"),
    ("post", "/api/enforcement/violations"),
    ("get", "/api/enforcement/violations"),
    ("get", "/api/enforcement/challans"),
    ("post", "/api/inspections/"),
    ("post", "/api/insurance/"),
    ("post", "/api/anpr/events"),
    ("get", "/api/anpr/events"),
    ("post", "/api/toll/events"),
    ("get", "/api/toll/transactions"),
    ("post", "/api/psv/operators"),
    ("get", "/api/psv/permits"),
    ("post", "/api/accidents/"),
    ("get", "/api/accidents/"),
    ("post", "/api/road-network/roads"),
]


@pytest.mark.parametrize("method,path", CITIZEN_FORBIDDEN)
def test_citizens_cannot_use_operational_endpoints(method, path):
    h, _ = _headers("citizen")
    assert getattr(client, method)(path, headers=h, **({"json": {}} if method == "post" else {})).status_code == 403


def test_citizen_cannot_clear_blacklist_or_extend_licence_or_delete_drivers():
    h, _ = _headers("citizen")
    vid, _ = _vehicle(is_blacklisted=True, blacklist_reason="stolen")
    assert client.patch(f"/api/vehicles/{vid}", json={"is_blacklisted": False}, headers=h).status_code == 403
    db = SessionLocal()
    d = db.query(Driver).first()
    did = d.id if d else None
    db.close()
    if did:
        assert client.patch(f"/api/drivers/{did}", json={"licence_expiry_date": "2099-01-01T00:00:00"},
                            headers=h).status_code == 403
        assert client.delete(f"/api/drivers/{did}", headers=h).status_code == 403


def test_toll_operator_can_work_the_gate_but_not_edit_registries():
    h, _ = _headers("toll_operator")
    _, plate = _vehicle(insured=True, fit=True)
    assert client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "G1"}, headers=h).status_code == 201
    assert client.get("/api/toll/transactions", headers=h).status_code == 200
    assert client.get(f"/api/vehicles/by-registration/{plate}", headers=h).status_code == 200
    assert client.post("/api/enforcement/violations", headers=h,
                       json={"violation_type": "speeding", "location": "x"}).status_code == 403
    assert client.post("/api/vehicles/", headers=h, json={}).status_code == 403


def test_officer_keeps_full_operational_access():
    h, _ = _headers("officer")
    vid, _ = _vehicle()
    r = client.post("/api/enforcement/violations", headers=h,
                    json={"vehicle_id": str(vid), "violation_type": "speeding", "location": "Great East Rd"})
    assert r.status_code == 201, r.text
    assert client.get("/api/enforcement/challans", headers=h).status_code == 200


# ============================ owner resolution ============================

def test_fine_notifies_the_owner_not_a_stranger_with_the_same_name():
    _rule("challan_created")
    officer, _ = _headers("officer")
    nrc = f"{uuid4().int % 10**6:06d}/22/1"
    _, owner_id = create_user("citizen", full_name="Mary Banda")
    _, stranger_id = create_user("citizen", full_name="Mary Banda")
    _link_driver(owner_id, nrc)
    vid, _ = _vehicle(nrc=nrc, owner_name="Mary Banda")
    r = client.post("/api/enforcement/violations", headers=officer,
                    json={"vehicle_id": str(vid), "violation_type": "speeding", "location": "Kafue Rd"})
    assert r.status_code == 201, r.text
    assert _notifications(owner_id, "challan_created") == 1
    assert _notifications(stranger_id, "challan_created") == 0


def test_portal_shows_fines_on_vehicles_linked_by_national_id():
    officer, _ = _headers("officer")
    nrc = f"{uuid4().int % 10**6:06d}/33/1"
    citizen, cid = _headers("citizen")
    _link_driver(cid, nrc)
    vid, _ = _vehicle(nrc=nrc)
    client.post("/api/enforcement/violations", headers=officer,
                json={"vehicle_id": str(vid), "violation_type": "speeding", "location": "Cairo Rd"})
    fines = client.get("/api/portal/fines", headers=citizen).json()
    assert len(fines) == 1
    pay = client.post(f"/api/portal/fines/{fines[0]['id']}/pay", headers=citizen)
    assert pay.status_code == 200, pay.text


# ============================ toll gate ============================

def test_repeated_gate_checks_do_not_pile_up_fines():
    _rule("toll_flagged")
    op, _ = _headers("toll_operator")
    nrc = f"{uuid4().int % 10**6:06d}/44/1"
    _, owner_id = create_user("citizen", full_name="Peter Zulu")
    _, stranger_id = create_user("citizen", full_name="Peter Zulu")
    _link_driver(owner_id, nrc)
    vid, plate = _vehicle(nrc=nrc, owner_name="Peter Zulu")  # uninsured, no fitness
    for gate in ("G1", "G2", "G3"):
        r = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": gate}, headers=op)
        assert r.status_code == 201 and r.json()["compliance_result"] == "flagged"
    assert _challans(vid) == 1  # one fine for the ongoing condition, not one per gate
    assert _notifications(owner_id, "toll_flagged") == 3
    assert _notifications(stranger_id, "toll_flagged") == 0


def test_unpaid_fine_alone_stops_the_vehicle_but_is_not_fined_again():
    op, _ = _headers("toll_operator")
    officer, _ = _headers("officer")
    vid, plate = _vehicle(insured=True, fit=True)
    client.post("/api/enforcement/violations", headers=officer,
                json={"vehicle_id": str(vid), "violation_type": "speeding", "location": "x"})
    before = _challans(vid)
    r = client.post("/api/toll/events", json={"plate_number": plate, "gate_id": "G9"}, headers=op).json()
    assert r["compliance_result"] == "flagged" and r["challan_created"] is False
    assert _challans(vid) == before


# ============================ licence workflow ============================

def _apply(citizen):
    r = client.post("/api/licence-applications/", headers=citizen, json={
        "first_name": "Ann", "last_name": "Phiri", "id_number": f"{uuid4().int % 10**6:06d}/55/1",
        "date_of_birth": "1995-05-05T00:00:00", "requested_class": "B"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_practical_requires_theory_pass_and_scores_are_bounded():
    citizen, _ = _headers("citizen")
    officer, _ = _headers("officer")
    app_id = _apply(citizen)
    assert client.post(f"/api/licence-applications/{app_id}/practical", json={"practical_score": 90},
                       headers=officer).status_code == 400
    assert client.post(f"/api/licence-applications/{app_id}/theory", json={"theory_score": 150},
                       headers=officer).status_code == 422
    client.post(f"/api/licence-applications/{app_id}/theory", json={"theory_score": 50}, headers=officer)
    assert client.post(f"/api/licence-applications/{app_id}/practical", json={"practical_score": 90},
                       headers=officer).status_code == 400  # failed theory


def test_issued_licence_reaches_the_applicant():
    _rule("licence_issued")
    citizen, cid = _headers("citizen")
    officer, _ = _headers("officer")
    app_id = _apply(citizen)
    for step, body in (("theory", {"theory_score": 80}), ("practical", {"practical_score": 85})):
        assert client.post(f"/api/licence-applications/{app_id}/{step}", json=body, headers=officer).status_code == 200
    issued = client.post(f"/api/licence-applications/{app_id}/issue", headers=officer)
    assert issued.status_code == 200 and issued.json()["status"] == "issued"
    number = issued.json()["issued_licence_number"]
    assert [d["licence_number"] for d in client.get("/api/citizen/licence", headers=citizen).json()] == [number]
    assert client.get("/api/portal/licence", headers=citizen).json()["licence_number"] == number
    assert _notifications(cid, "licence_issued") == 1


def test_licence_expiry_is_safe_on_leap_day(monkeypatch):
    import app.api.licence as licence

    class LeapDay(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2028, 2, 29, 12, 0)

    monkeypatch.setattr(licence, "datetime", LeapDay)
    assert licence._years_from_now(5) == datetime(2033, 2, 28, 12, 0)


# ============================ platform ============================

def test_duplicate_unique_value_is_a_409_not_a_500():
    quiet = TestClient(app, raise_server_exceptions=False)
    h, _ = _headers("admin")
    body = {"name": f"Audit Junction {uuid4().hex[:4]}", "latitude": -15.4, "longitude": 28.3}
    assert quiet.post("/api/road-network/intersections", json=body, headers=h).status_code == 201
    assert quiet.post("/api/road-network/intersections", json=body, headers=h).status_code == 409


def test_access_tokens_are_redacted_from_access_logs():
    from app.core.logging import RedactTokens

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/api/events/stream?token=eyJhbGciOi.secret.sig", "1.1", 200), None)
    RedactTokens().filter(record)
    assert "eyJhbGciOi" not in record.getMessage() and "token=[redacted]" in record.getMessage()


def test_repair_migration_heals_a_database_stamped_past_the_platform_migration(tmp_path, monkeypatch):
    """Reproduces the Neon failure: alembic_version says a7c3d91e5b20 ran, but its
    tables and columns are missing. Upgrading must converge the schema."""
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    from app.core.config import settings

    url = f"sqlite:///{(tmp_path / 'neon.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "f0a1f8296810")
    command.stamp(cfg, "a7c3d91e5b20")
    insp = sa.inspect(sa.create_engine(url))
    assert "phone_number" not in {c["name"] for c in insp.get_columns("users")}

    command.upgrade(cfg, "heads")
    insp = sa.inspect(sa.create_engine(url))
    assert {"phone_number", "mfa_enabled", "locked_until"} <= {c["name"] for c in insp.get_columns("users")}
    for table in ("notification_preferences", "devices", "user_sessions", "login_attempts", "system_settings",
                  "agency_clients", "payment_events"):
        assert table in insp.get_table_names(), table
    # and on a healthy database it is a no-op: a fresh database runs the full chain cleanly
    fresh = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", fresh)
    command.upgrade(cfg, "heads")
    assert "notification_preferences" in sa.inspect(sa.create_engine(fresh)).get_table_names()


@pytest.mark.parametrize("given,expected", [
    ("postgresql://u:p@db.neon.tech/rtsa?sslmode=require", "postgresql+psycopg2://u:p@db.neon.tech/rtsa?sslmode=require"),
    ("postgres://u:p@host:5432/rtsa", "postgresql+psycopg2://u:p@host:5432/rtsa"),
    ("postgresql+psycopg2://u:p@host/rtsa", "postgresql+psycopg2://u:p@host/rtsa"),
    ("sqlite:///./test.db", "sqlite:///./test.db"),
])
def test_database_url_uses_the_installed_postgres_driver(given, expected):
    """SQLAlchemy 2.1 defaults postgresql:// to psycopg 3, which isn't installed: without
    naming psycopg2 a fresh install (and every new Render deploy) can't reach the database."""
    from app.core.config import Settings

    assert Settings(DATABASE_URL=given).DATABASE_URL == expected
