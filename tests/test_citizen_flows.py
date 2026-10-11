"""The citizen side of the RTSA-citizen loop: rules that keep it honest.

Licence renewal can't lift a suspension or stack years early, a licence
application is the applicant's own and one at a time, field-staff lookups
tolerate how plates and licence numbers are typed, and the portal's counts and
fine details match what the rest of the app shows.
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

from app.models.driver import Driver, DriverStatus, LicenceClass  # noqa: E402
from app.models.notification import Notification, NotificationChannel, NotificationStatus  # noqa: E402
from main import app  # noqa: E402
from tests.conftest import create_user, random_nrc  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _login(role: str, **extra) -> tuple[dict, object]:
    email, user_id = create_user(role, **extra)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, user_id


def _licence(user_id, expires_in_days: int, status: DriverStatus = DriverStatus.ACTIVE) -> str:
    licence = f"CF-{uuid4().hex[:6].upper()}"
    db = SessionLocal()
    try:
        db.add(Driver(licence_number=licence, first_name="Mwila", last_name="Zulu", id_number=random_nrc(),
                      date_of_birth=datetime(1990, 3, 3), licence_class=LicenceClass.B,
                      licence_issue_date=datetime.utcnow() - timedelta(days=1000),
                      licence_expiry_date=datetime.utcnow() + timedelta(days=expires_in_days),
                      status=status, user_id=user_id))
        db.commit()
        return licence
    finally:
        db.close()


# --------------------------------- renewal ---------------------------------

def test_renewal_is_offered_and_allowed_inside_the_window():
    h, user_id = _login("citizen")
    _licence(user_id, expires_in_days=20)
    lic = client.get("/api/portal/licence", headers=h).json()
    assert lic["can_renew"] is True
    r = client.post("/api/portal/licence/renew", headers=h)
    assert r.status_code == 200, r.text


def test_renewing_years_early_is_refused():
    h, user_id = _login("citizen")
    _licence(user_id, expires_in_days=900)
    assert client.get("/api/portal/licence", headers=h).json()["can_renew"] is False
    r = client.post("/api/portal/licence/renew", headers=h)
    assert r.status_code == 409
    assert "Renewal opens" in r.json()["detail"]


def test_paying_the_renewal_fee_cannot_lift_a_suspension():
    h, user_id = _login("citizen")
    _licence(user_id, expires_in_days=10, status=DriverStatus.SUSPENDED)
    assert client.get("/api/portal/licence", headers=h).json()["can_renew"] is False
    r = client.post("/api/portal/licence/renew", headers=h)
    assert r.status_code == 409
    assert "suspended" in r.json()["detail"]


def test_an_expired_licence_can_be_renewed_and_is_active_again():
    h, user_id = _login("citizen")
    _licence(user_id, expires_in_days=-5, status=DriverStatus.EXPIRED)
    r = client.post("/api/portal/licence/renew", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["state"] == "valid"


# --------------------------- licence applications ---------------------------

def _apply(h: dict, nrc: str, **extra):
    body = {"first_name": "Ann", "last_name": "Phiri", "id_number": nrc,
            "date_of_birth": "1998-05-05T00:00:00", "requested_class": "B"}
    body.update(extra)
    return client.post("/api/licence-applications/", headers=h, json=body)


def test_an_application_is_stored_with_a_clean_nrc():
    h, _ = _login("citizen")
    nrc = random_nrc()
    r = _apply(h, nrc.replace("/", ""))  # typed without the slashes
    assert r.status_code == 201, r.text
    assert r.json()["id_number"] == nrc


def test_only_one_application_at_a_time():
    h, _ = _login("citizen")
    nrc = random_nrc()
    assert _apply(h, nrc).status_code == 201
    second = _apply(h, nrc, requested_class="C")
    assert second.status_code == 409


def test_a_citizen_applies_with_their_own_nrc():
    nrc = random_nrc()
    h, _ = _login("citizen", nrc_number=nrc)
    assert _apply(h, random_nrc()).status_code == 422
    assert _apply(h, nrc).status_code == 201


@pytest.mark.parametrize("bad", [
    {"first_name": "   "},
    {"id_number": "not-an-nrc"},
    {"date_of_birth": "2999-01-01T00:00:00"},
])
def test_application_fields_are_checked(bad):
    h, _ = _login("citizen")
    body = {"id_number": random_nrc()}
    body.update(bad)
    nrc = body.pop("id_number")
    assert _apply(h, nrc, **body).status_code == 422


# ------------------------------ lookups by staff ------------------------------

def test_plate_and_licence_lookups_ignore_spacing_and_case():
    officer, _ = _login("officer")
    reg = f"CF {uuid4().hex[:4].upper()}"
    client.post("/api/vehicles/", headers=officer, json={
        "registration_number": reg, "owner_name": "X", "owner_id_number": uuid4().hex[:8],
        "make": "Kia", "model": "Rio", "year": 2018})
    r = client.get(f"/api/vehicles/by-registration/{reg.replace(' ', '').lower()}", headers=officer)
    assert r.status_code == 200 and r.json()["registration_number"] == reg

    licence = _licence(None, expires_in_days=400)
    r = client.get(f"/api/drivers/by-licence/{licence.lower()}%20", headers=officer)
    assert r.status_code == 200 and r.json()["licence_number"] == licence


# ------------------------------- portal figures -------------------------------

def test_unread_count_matches_the_bell():
    h, user_id = _login("citizen")
    db = SessionLocal()
    try:
        # One event delivered on two channels: one thing to read in the app.
        for channel in (NotificationChannel.IN_APP, NotificationChannel.EMAIL):
            db.add(Notification(user_id=user_id, channel=channel, trigger_event="x", title="t", body="b",
                                status=NotificationStatus.SENT))
        db.commit()
    finally:
        db.close()
    bell = client.get("/api/notifications/unread-count", headers=h).json()["unread"]
    assert client.get("/api/portal/dashboard", headers=h).json()["unread_notifications"] == bell == 1


def test_a_fine_says_which_vehicle_it_was():
    officer, _ = _login("officer")
    nrc = random_nrc()
    citizen, user_id = _login("citizen")
    db = SessionLocal()
    try:
        db.add(Driver(licence_number=f"CF-{uuid4().hex[:6]}", first_name="A", last_name="B", id_number=nrc,
                      date_of_birth=datetime(1990, 1, 1), licence_class=LicenceClass.B,
                      licence_issue_date=datetime.utcnow(), licence_expiry_date=datetime.utcnow() + timedelta(days=500),
                      user_id=user_id))
        db.commit()
    finally:
        db.close()
    reg = f"CF {uuid4().hex[:4].upper()}"
    vid = client.post("/api/vehicles/", headers=officer, json={
        "registration_number": reg, "owner_name": "A B", "owner_id_number": nrc,
        "make": "Kia", "model": "Rio", "year": 2018}).json()["id"]
    client.post("/api/enforcement/violations", headers=officer,
                json={"vehicle_id": vid, "violation_type": "no_insurance", "location": "Cairo Road"})
    fines = client.get("/api/portal/fines", headers=citizen).json()
    assert [f["registration_number"] for f in fines] == [reg]
