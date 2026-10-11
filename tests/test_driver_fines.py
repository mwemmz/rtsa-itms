"""Fines a citizen owes as the driver show up in their portal and can be paid.

A fine recorded against a driver's licence with no vehicle reached nobody: the
portal only looked at fines on the user's vehicles or charged to their account.
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
from main import app  # noqa: E402
from tests.conftest import create_user  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _login(role: str, **extra) -> tuple[dict, object, str]:
    email, user_id = create_user(role, **extra)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, user_id, email


def _driver(user_id=None, email=None, id_number=None) -> tuple[str, str]:
    licence = f"DF-{uuid4().hex[:8].upper()}"
    db = SessionLocal()
    try:
        d = Driver(licence_number=licence, first_name="Chanda", last_name="Phiri",
                   id_number=id_number or uuid4().hex[:8], date_of_birth=datetime(1992, 5, 5),
                   licence_class=LicenceClass.B, licence_issue_date=datetime.utcnow() - timedelta(days=300),
                   licence_expiry_date=datetime.utcnow() + timedelta(days=300), user_id=user_id, email=email)
        db.add(d)
        db.commit()
        return str(d.id), licence
    finally:
        db.close()


def _vehicle(officer: dict, owner_id_number: str | None = None) -> str:
    r = client.post("/api/vehicles/", headers=officer, json={
        "registration_number": f"DF {uuid4().hex[:4].upper()}", "owner_name": "Someone Else",
        "owner_id_number": owner_id_number or uuid4().hex[:8], "make": "Mazda", "model": "Demio", "year": 2014,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _record(officer: dict, **payload) -> dict:
    payload.setdefault("location", "Cairo Road")
    r = client.post("/api/enforcement/violations", headers=officer, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _portal_refs(h: dict) -> set[str]:
    return {f["reference"] for f in client.get("/api/portal/fines", headers=h).json()}


def test_a_licence_only_fine_reaches_the_driver_and_can_be_paid():
    officer, _, _ = _login("officer")
    citizen, user_id, _ = _login("citizen")
    driver_id, _ = _driver(user_id=user_id)
    ref = _record(officer, driver_id=driver_id, violation_type="using_phone")["challan"]["reference"]

    assert ref in _portal_refs(citizen)
    fine = next(f for f in client.get("/api/portal/fines", headers=citizen).json() if f["reference"] == ref)
    pay = client.post(f"/api/portal/fines/{fine['id']}/pay", headers=citizen)
    assert pay.status_code == 200, pay.text
    assert ref not in _portal_refs(citizen)  # paid fines drop off the default list


def test_someone_else_cannot_see_or_pay_a_drivers_fine():
    officer, _, _ = _login("officer")
    _, driver_user, _ = _login("citizen")
    bystander, _, _ = _login("citizen")
    driver_id, _ = _driver(user_id=driver_user)
    fine = _record(officer, driver_id=driver_id, violation_type="reckless_driving")["challan"]

    assert fine["reference"] not in _portal_refs(bystander)
    assert client.post(f"/api/portal/fines/{fine['id']}/pay", headers=bystander).status_code == 403


def test_a_vehicle_offence_stays_with_the_owner_not_the_driver():
    officer, _, _ = _login("officer")
    citizen, user_id, _ = _login("citizen")
    driver_id, _ = _driver(user_id=user_id)
    vid = _vehicle(officer)  # someone else's vehicle
    fine = _record(officer, vehicle_id=vid, driver_id=driver_id, violation_type="expired_fitness")["challan"]

    assert fine["reference"] not in _portal_refs(citizen)
    assert client.post(f"/api/portal/fines/{fine['id']}/pay", headers=citizen).status_code == 403


def test_a_driver_offence_in_someone_elses_car_reaches_the_driver():
    officer, _, _ = _login("officer")
    citizen, user_id, _ = _login("citizen")
    driver_id, _ = _driver(user_id=user_id)
    vid = _vehicle(officer)
    ref = _record(officer, vehicle_id=vid, driver_id=driver_id,
                  violation_type="running_red_light")["challan"]["reference"]
    assert ref in _portal_refs(citizen)


def test_a_driver_named_later_then_sees_the_fine():
    officer, _, _ = _login("officer")
    citizen, user_id, _ = _login("citizen")
    _, licence = _driver(user_id=user_id)
    vid = _vehicle(officer)
    created = _record(officer, vehicle_id=vid, violation_type="reckless_driving",
                      evidence_source="citizen_video", evidence_url="https://example.com/v/9")
    assert created["challan"]["reference"] not in _portal_refs(citizen)

    r = client.post(f"/api/enforcement/violations/{created['id']}/driver", headers=officer,
                    json={"licence_number": licence})
    assert r.status_code == 200, r.text
    assert created["challan"]["reference"] in _portal_refs(citizen)


def test_an_owner_linked_by_email_can_pay_what_they_are_shown():
    # The portal listed these (owner found through a driver record carrying the
    # account's email) but paying was refused, because the two checks differed.
    officer, _, _ = _login("officer")
    nrc = uuid4().hex[:8]
    citizen, _, email = _login("citizen")
    _driver(email=email, id_number=nrc)  # not linked to the account by id
    vid = _vehicle(officer, owner_id_number=nrc)
    fine = _record(officer, vehicle_id=vid, violation_type="no_insurance")["challan"]

    assert fine["reference"] in _portal_refs(citizen)
    assert client.post(f"/api/portal/fines/{fine['id']}/pay", headers=citizen).status_code == 200
