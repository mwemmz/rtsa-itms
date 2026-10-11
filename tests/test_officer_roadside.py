"""The officer's roadside work after a violation is recorded.

Covers the ticket (PDF and SMS), telling the driver as well as the owner,
"recorded by me", naming the driver later for a camera or video case, where
the evidence came from, and ANPR plate matching and alerts.
"""

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
import app.models  # noqa: F401, E402  # register all models on Base.metadata
import app.core.ratelimit as ratelimit  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402

Base.metadata.create_all(bind=engine)

from app.models.driver import Driver, LicenceClass  # noqa: E402
from app.models.enforcement import Challan  # noqa: E402
from app.models.notification import Notification, NotificationRule  # noqa: E402
from main import app  # noqa: E402
from tests.conftest import create_user  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


@pytest.fixture(autouse=True)
def _challan_rule():
    db = SessionLocal()
    try:
        if not db.query(NotificationRule).filter(NotificationRule.trigger_event == "challan_created").first():
            db.add(NotificationRule(trigger_event="challan_created", channels="in_app",
                                    title_template="e-Challan issued", body_template="{reference}"))
            db.commit()
    finally:
        db.close()


def _login(role: str) -> tuple[dict, object]:
    email, user_id = create_user(role)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, user_id


def _vehicle(h: dict, owner_id_number: str | None = None) -> tuple[str, str]:
    reg = f"RD {uuid4().hex[:4].upper()}"
    r = client.post("/api/vehicles/", headers=h, json={
        "registration_number": reg, "owner_name": "Plate Owner",
        "owner_id_number": owner_id_number or uuid4().hex[:8], "make": "Toyota", "model": "Hilux", "year": 2020,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"], reg


def _driver(user_id=None) -> tuple[str, str]:
    licence = f"DL-{uuid4().hex[:8].upper()}"
    db = SessionLocal()
    try:
        d = Driver(licence_number=licence, first_name="Mutale", last_name="Banda", id_number=uuid4().hex[:8],
                   date_of_birth=datetime(1990, 1, 1), licence_class=LicenceClass.B,
                   licence_issue_date=datetime.utcnow() - timedelta(days=400),
                   licence_expiry_date=datetime.utcnow() + timedelta(days=400), user_id=user_id)
        db.add(d)
        db.commit()
        return str(d.id), licence
    finally:
        db.close()


def _record(h: dict, **payload):
    payload.setdefault("location", "Great East Road")
    return client.post("/api/enforcement/violations", headers=h, json=payload)


def _challan_notes(user_id) -> int:
    db = SessionLocal()
    try:
        return db.query(Notification).filter(Notification.user_id == user_id,
                                             Notification.trigger_event == "challan_created").count()
    finally:
        db.close()


# ---------------------------------- the ticket ----------------------------------

def test_recording_returns_the_challan_for_the_ticket():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    r = _record(h, vehicle_id=vid, violation_type="no_insurance")
    assert r.status_code == 201, r.text
    challan = r.json()["challan"]
    assert challan["reference"].startswith("CH-")
    assert challan["status"] == "unpaid"
    assert challan["penalty_amount"] > 0


def test_ticket_pdf_is_printable_by_officers_only():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    challan_id = _record(h, vehicle_id=vid, violation_type="speeding", speed_kmh=104, speed_limit_kmh=80,
                         latitude=-15.4, longitude=28.3).json()["challan"]["id"]

    pdf = client.get(f"/api/enforcement/challans/{challan_id}/ticket.pdf", headers=h)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    citizen, _ = _login("citizen")
    assert client.get(f"/api/enforcement/challans/{challan_id}/ticket.pdf", headers=citizen).status_code == 403


def test_ticket_sms_goes_to_the_number_given(monkeypatch):
    sent = []
    monkeypatch.setattr("app.api.enforcement.send_sms", lambda to, text: sent.append((to, text)))
    h, _ = _login("officer")
    vid, reg = _vehicle(h)
    challan = _record(h, vehicle_id=vid, violation_type="illegal_parking").json()["challan"]

    r = client.post(f"/api/enforcement/challans/{challan['id']}/sms", headers=h,
                    json={"phone_number": "097 712 3456"})
    assert r.status_code == 200, r.text
    assert r.json()["to"].endswith("456") and "*" in r.json()["to"]
    assert sent[0][0] == "0977123456"
    assert challan["reference"] in sent[0][1] and reg in sent[0][1]


def test_ticket_sms_rejects_something_that_isnt_a_phone_number():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    challan_id = _record(h, vehicle_id=vid, violation_type="illegal_parking").json()["challan"]["id"]
    r = client.post(f"/api/enforcement/challans/{challan_id}/sms", headers=h, json={"phone_number": "call me"})
    assert r.status_code == 422


def test_ticket_sms_failure_is_reported(monkeypatch):
    def boom(to, text):
        raise RuntimeError("gateway down")
    monkeypatch.setattr("app.api.enforcement.send_sms", boom)
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    challan_id = _record(h, vehicle_id=vid, violation_type="illegal_parking").json()["challan"]["id"]
    r = client.post(f"/api/enforcement/challans/{challan_id}/sms", headers=h, json={"phone_number": "0977123456"})
    assert r.status_code == 502


# ------------------------------ telling the driver ------------------------------

def test_the_driver_is_notified_of_a_driver_offence():
    h, _ = _login("officer")
    _, driver_user = create_user("citizen")
    driver_id, _ = _driver(user_id=driver_user)
    vid, _ = _vehicle(h)
    r = _record(h, vehicle_id=vid, driver_id=driver_id, violation_type="using_phone")
    assert r.status_code == 201, r.text
    assert _challan_notes(driver_user) == 1


def test_the_driver_is_not_notified_of_a_vehicle_offence():
    h, _ = _login("officer")
    _, driver_user = create_user("citizen")
    driver_id, _ = _driver(user_id=driver_user)
    vid, _ = _vehicle(h)
    assert _record(h, vehicle_id=vid, driver_id=driver_id, violation_type="expired_fitness").status_code == 201
    assert _challan_notes(driver_user) == 0


# ------------------------------- recorded by me -------------------------------

def test_mine_lists_only_what_the_caller_recorded():
    me, _ = _login("officer")
    colleague, _ = _login("officer")
    vid, _ = _vehicle(me)
    mine = _record(me, vehicle_id=vid, violation_type="illegal_parking").json()["id"]
    theirs = _record(colleague, vehicle_id=vid, violation_type="illegal_parking").json()["id"]

    listed = [v["id"] for v in client.get("/api/enforcement/violations?mine=true", headers=me).json()]
    assert mine in listed and theirs not in listed


# --------------------------- naming the driver later ---------------------------

def test_the_driver_can_be_named_after_a_video_case():
    h, _ = _login("officer")
    _, driver_user = create_user("citizen")
    _, licence = _driver(user_id=driver_user)
    vid, _ = _vehicle(h)
    created = _record(h, vehicle_id=vid, violation_type="reckless_driving",
                      evidence_source="citizen_video", evidence_url="https://example.com/clip/1").json()
    assert created["driver_name"] is None

    r = client.post(f"/api/enforcement/violations/{created['id']}/driver", headers=h,
                    json={"licence_number": licence})
    assert r.status_code == 200, r.text
    assert r.json()["driver_name"] == "Mutale Banda"
    assert _challan_notes(driver_user) == 1

    db = SessionLocal()
    try:
        challan = db.query(Challan).filter(Challan.reference == created["challan"]["reference"]).one()
        assert challan.driver_id is not None
    finally:
        db.close()

    again = client.post(f"/api/enforcement/violations/{created['id']}/driver", headers=h,
                        json={"licence_number": licence})
    assert again.status_code == 409


def test_naming_an_unknown_licence_is_refused():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    vio = _record(h, vehicle_id=vid, violation_type="reckless_driving").json()["id"]
    r = client.post(f"/api/enforcement/violations/{vio}/driver", headers=h, json={"licence_number": "NOPE-1"})
    assert r.status_code == 404


# ------------------------------- where evidence came from -------------------------------

def test_evidence_source_and_link_are_kept():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    r = _record(h, vehicle_id=vid, violation_type="reckless_driving",
                evidence_source="social_media", evidence_url="https://facebook.com/post/123")
    assert r.status_code == 201, r.text
    assert r.json()["evidence_source"] == "social_media"
    assert r.json()["evidence_url"] == "https://facebook.com/post/123"


def test_a_social_media_case_needs_the_link():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    r = _record(h, vehicle_id=vid, violation_type="reckless_driving", evidence_source="social_media")
    assert r.status_code == 422
    assert "link" in r.text


def test_evidence_link_must_be_a_web_address():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    r = _record(h, vehicle_id=vid, violation_type="reckless_driving",
                evidence_source="citizen_video", evidence_url="javascript:alert(1)")
    assert r.status_code == 422


def test_unknown_evidence_source_is_rejected():
    h, _ = _login("officer")
    vid, _ = _vehicle(h)
    r = _record(h, vehicle_id=vid, violation_type="reckless_driving", evidence_source="rumour")
    assert r.status_code == 422


# ------------------------------------- ANPR -------------------------------------

def test_anpr_matches_a_plate_however_it_was_written():
    h, _ = _login("officer")
    vid, reg = _vehicle(h)
    r = client.post("/api/anpr/events", headers=h,
                    json={"plate_number": reg.replace(" ", "").lower(), "location": "Kafue Road, camera 2"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["vehicle_id"] == vid
    assert body["plate_number"] == reg

    listed = client.get(f"/api/anpr/events?plate_number={reg.lower()}", headers=h).json()
    assert [e["id"] for e in listed] == [body["id"]]


def test_anpr_flags_a_non_compliant_vehicle_to_stop():
    h, _ = _login("officer")
    _, reg = _vehicle(h)  # no insurance on record
    body = client.post("/api/anpr/events", headers=h, json={"plate_number": reg, "location": "Cairo Road"}).json()
    assert body["decision"] == "stop"
    assert any("insurance" in reason.lower() for reason in body["reasons"])


def test_anpr_flags_an_unregistered_plate():
    h, _ = _login("officer")
    body = client.post("/api/anpr/events", headers=h,
                       json={"plate_number": f"ZZ {uuid4().hex[:4]}", "location": "Cairo Road"}).json()
    assert body["vehicle_id"] is None
    assert body["decision"] == "stop"
    assert body["reasons"] == ["No vehicle is registered with this plate"]


def test_anpr_rejects_a_blank_plate():
    h, _ = _login("officer")
    r = client.post("/api/anpr/events", headers=h, json={"plate_number": "   ", "location": "Cairo Road"})
    assert r.status_code == 422
