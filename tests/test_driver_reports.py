"""Citizens report dangerous driving with evidence; officers review and fine it.

From the citizen's report (plate, what happened, a video link and/or photos,
tied to their NRC) through the officer's decision: accept (a violation and
fine on the vehicle, with the photos moved onto it), reject (nobody blamed),
or reject as false (the reporter is fined and it counts as a strike).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
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

from app.models.enforcement import Challan, Violation  # noqa: E402
from app.models.notification import Notification, NotificationChannel, NotificationRule  # noqa: E402
from main import app  # noqa: E402
from tests.conftest import create_user, random_nrc  # noqa: E402

client = TestClient(app)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + os.urandom(1500)
EVENTS = ("driver_report_received", "driver_report_accepted", "driver_report_rejected",
          "driver_report_false", "challan_created")


@pytest.fixture(autouse=True)
def _setup():
    db = SessionLocal()
    try:
        for event in EVENTS:
            if not db.query(NotificationRule).filter(NotificationRule.trigger_event == event).first():
                db.add(NotificationRule(trigger_event=event, channels="in_app", title_template=event,
                                        body_template=event))
        db.commit()
    finally:
        db.close()
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _login(role: str, **extra) -> tuple[dict, object]:
    email, user_id = create_user(role, **extra)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, user_id


def _citizen() -> tuple[dict, object]:
    return _login("citizen", nrc_number=random_nrc())


def _vehicle(officer: dict) -> tuple[str, str]:
    reg = f"DRV {uuid4().hex[:4].upper()}"
    r = client.post("/api/vehicles/", headers=officer, json={
        "registration_number": reg, "owner_name": "Reported Owner", "owner_id_number": uuid4().hex[:8],
        "make": "Toyota", "model": "Corolla", "year": 2017})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"], reg


def _report(h: dict, plate: str, photos: int = 0, **fields):
    data = {
        "plate_number": plate, "violation_type": "reckless_driving",
        "location": "Great East Road near Arcades", "description": "Overtook on a solid line into oncoming traffic",
        "observed_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "declaration": "true", "evidence_url": "https://facebook.com/watch/123",
    }
    data.update({k: v for k, v in fields.items() if v is not None})
    for k in [k for k, v in fields.items() if v is None]:
        data.pop(k, None)
    files = [("photos", (f"p{i}.jpg", JPEG, "image/jpeg")) for i in range(photos)]
    return client.post("/api/driver-reports/", headers=h, data=data, files=files or None)


def _events(user_id, event) -> int:
    """In-app notices only: the same event may also go out by email, depending on
    which rule another test file seeded first."""
    db = SessionLocal()
    try:
        return db.query(Notification).filter(Notification.user_id == user_id,
                                             Notification.trigger_event == event,
                                             Notification.channel == NotificationChannel.IN_APP).count()
    finally:
        db.close()


# ------------------------------- the citizen's side -------------------------------

def test_a_citizen_reports_a_driver_with_a_video_link_and_photos():
    officer, officer_id = _login("officer")
    _, reg = _vehicle(officer)
    citizen, _ = _citizen()
    r = _report(citizen, reg.replace(" ", "").lower(), photos=2)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["plate_number"] == reg  # matched and stored as registered
    assert len(body["photos"]) == 2 and body["reference"].startswith("DR-")
    assert _events(officer_id, "driver_report_received") >= 1  # officers hear at once

    mine = client.get("/api/driver-reports/mine", headers=citizen).json()
    assert [m["id"] for m in mine] == [body["id"]]


def test_photos_alone_are_enough_evidence():
    citizen, _ = _citizen()
    r = _report(citizen, "ABC 123", photos=1, evidence_url=None)
    assert r.status_code == 201, r.text


def test_a_report_needs_some_evidence():
    citizen, _ = _citizen()
    r = _report(citizen, "ABC 123", evidence_url=None)
    assert r.status_code == 422
    assert "video" in r.json()["detail"]


def test_a_report_needs_an_nrc_on_the_account():
    h, _ = _login("citizen")
    r = _report(h, "ABC 123")
    assert r.status_code == 403
    assert "NRC" in r.json()["detail"]


def test_staff_record_violations_instead_of_reporting():
    officer, _ = _login("officer")
    assert _report(officer, "ABC 123").status_code == 403


@pytest.mark.parametrize("bad", [
    {"declaration": "false"},
    {"violation_type": "speeding"},          # needs a measured speed
    {"violation_type": "false_report"},
    {"description": "bad"},                  # too short to act on
    {"evidence_url": "javascript:alert(1)"},
    {"observed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"observed_at": (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()},
])
def test_report_fields_are_checked(bad):
    citizen, _ = _citizen()
    assert _report(citizen, "ABC 123", **bad).status_code == 422


def test_a_script_posing_as_a_photo_is_refused():
    citizen, _ = _citizen()
    files = [("photos", ("evil.jpg", b"<script>alert(1)</script>", "image/jpeg"))]
    data = {"plate_number": "ABC 123", "violation_type": "reckless_driving", "location": "Cairo Road",
            "description": "Drove onto the pavement at speed", "declaration": "true",
            "observed_at": datetime.now(timezone.utc).isoformat()}
    r = client.post("/api/driver-reports/", headers=citizen, data=data, files=files)
    assert r.status_code == 415


def test_open_reports_are_capped():
    citizen, _ = _citizen()
    for _ in range(3):
        assert _report(citizen, "ABC 123").status_code == 201
    r = _report(citizen, "ABC 123")
    assert r.status_code == 403
    assert "waiting" in r.json()["detail"]


def test_report_photos_are_private_to_the_reporter_and_officers():
    citizen, _ = _citizen()
    body = _report(citizen, "ABC 123", photos=1).json()
    url = f"/api/driver-reports/{body['id']}/photos/{body['photos'][0]['id']}"
    assert client.get(url, headers=citizen).content == JPEG
    officer, _ = _login("officer")
    assert client.get(url, headers=officer).status_code == 200
    stranger, _ = _citizen()
    assert client.get(url, headers=stranger).status_code == 404


# ------------------------------- the officer's side -------------------------------

def test_officers_see_waiting_reports_with_who_sent_them():
    officer, _ = _login("officer")
    _, reg = _vehicle(officer)
    citizen, _ = _citizen()
    rid = _report(citizen, reg).json()["id"]
    queue = client.get("/api/driver-reports/", headers=officer).json()
    item = next(r for r in queue if r["id"] == rid)
    assert item["reporter_nrc"] and item["reporter_false_reports"] == 0
    assert item["vehicle"]["registration_number"] == reg
    assert client.get("/api/driver-reports/", headers=citizen).status_code == 403


def test_accepting_a_report_fines_the_vehicle_and_moves_the_evidence():
    officer, _ = _login("officer")
    vid, reg = _vehicle(officer)
    citizen, citizen_id = _citizen()
    rid = _report(citizen, reg, photos=2).json()["id"]

    r = client.post(f"/api/driver-reports/{rid}/accept", headers=officer, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted" and body["challan_reference"].startswith("CH-")
    assert _events(citizen_id, "driver_report_accepted") == 1

    listed = client.get(f"/api/enforcement/violations?vehicle_id={vid}", headers=officer).json()
    assert len(listed) == 1
    violation = listed[0]
    assert violation["violation_type"] == "reckless_driving"
    assert violation["evidence_source"] == "citizen_report"
    assert violation["evidence_url"] == "https://facebook.com/watch/123"
    assert violation["photo_count"] == 2

    again = client.post(f"/api/driver-reports/{rid}/accept", headers=officer, json={})
    assert again.status_code == 409


def test_the_officer_can_correct_the_plate_and_offence():
    officer, _ = _login("officer")
    vid, reg = _vehicle(officer)
    citizen, _ = _citizen()
    rid = _report(citizen, "NOT 0000").json()["id"]  # misread plate: nothing matches

    blocked = client.post(f"/api/driver-reports/{rid}/accept", headers=officer, json={})
    assert blocked.status_code == 422

    r = client.post(f"/api/driver-reports/{rid}/accept", headers=officer,
                    json={"plate_number": reg, "violation_type": "running_red_light", "note": "Plate read from the video"})
    assert r.status_code == 200, r.text
    violation = client.get(f"/api/enforcement/violations?vehicle_id={vid}", headers=officer).json()[0]
    assert violation["violation_type"] == "running_red_light"


def test_turning_a_report_into_speeding_is_refused_without_a_reading():
    officer, _ = _login("officer")
    _, reg = _vehicle(officer)
    citizen, _ = _citizen()
    rid = _report(citizen, reg).json()["id"]
    r = client.post(f"/api/driver-reports/{rid}/accept", headers=officer, json={"violation_type": "speeding"})
    assert r.status_code == 422


def test_rejecting_a_report_blames_nobody():
    officer, _ = _login("officer")
    citizen, citizen_id = _citizen()
    rid = _report(citizen, "ABC 123").json()["id"]
    r = client.post(f"/api/driver-reports/{rid}/reject", headers=officer, json={"reason": "Plate not readable in the video"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _events(citizen_id, "driver_report_rejected") == 1
    standing = client.get("/api/incidents/reporting-status", headers=citizen).json()
    assert standing["false_reports"] == 0


def test_a_false_report_fines_the_reporter_and_counts_as_a_strike():
    officer, _ = _login("officer")
    citizen, citizen_id = _citizen()
    rid = _report(citizen, "ABC 123").json()["id"]
    r = client.post(f"/api/driver-reports/{rid}/reject", headers=officer,
                    json={"reason": "The video shows a different car", "false_report": True})
    assert r.status_code == 200 and r.json()["status"] == "false"
    assert _events(citizen_id, "driver_report_false") == 1

    db = SessionLocal()
    try:
        fine = (db.query(Challan).join(Violation, Violation.id == Challan.violation_id)
                .filter(Challan.user_id == citizen_id).one())
        assert fine.penalty_amount > 0
    finally:
        db.close()
    standing = client.get("/api/incidents/reporting-status", headers=citizen).json()
    assert standing["false_reports"] == 1
