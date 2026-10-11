"""What a violation has to carry to stand up when it is disputed.

Covers the speeding evidence rule (speed + limit, speed over the limit), the
offence-time rules (not in the future; older than a day needs a reason), the
officer's GPS fix, and the dashboard's 24-hour count.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TRUST_PROXY_HEADERS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402
import app.models  # noqa: F401, E402  # register all models on Base.metadata
import app.core.ratelimit as ratelimit  # noqa: E402
from app.core.database import Base, engine  # noqa: E402

Base.metadata.create_all(bind=engine)

from main import app  # noqa: E402
from tests.conftest import create_user  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _officer() -> dict:
    email, _ = create_user("officer")
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _vehicle(headers: dict, reg: str, id_number: str) -> str:
    resp = client.post(
        "/api/vehicles/",
        json={
            "registration_number": reg,
            "owner_name": "Evidence Owner",
            "owner_id_number": id_number,
            "make": "Toyota",
            "model": "Corolla",
            "year": 2019,
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _record(headers: dict, **payload):
    return client.post("/api/enforcement/violations", json=payload, headers=headers)


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


# ----------------------------- speeding evidence -----------------------------

def test_speeding_needs_the_speed_and_the_limit():
    h = _officer()
    vid = _vehicle(h, "EVD 701", "701011")

    no_limit = _record(h, vehicle_id=vid, violation_type="speeding", location="Great East Rd", speed_kmh=110)
    assert no_limit.status_code == 422
    assert "speed limit" in no_limit.text

    no_speed = _record(h, vehicle_id=vid, violation_type="speeding", location="Great East Rd", speed_limit_kmh=80)
    assert no_speed.status_code == 422


def test_speeding_must_be_over_the_limit():
    h = _officer()
    vid = _vehicle(h, "EVD 702", "702011")
    r = _record(h, vehicle_id=vid, violation_type="speeding", location="Kafue Rd",
                speed_kmh=80, speed_limit_kmh=80)
    assert r.status_code == 422
    assert "not over" in r.text


def test_speeding_evidence_round_trips_through_the_list():
    h = _officer()
    vid = _vehicle(h, "EVD 703", "703011")
    created = _record(h, vehicle_id=vid, violation_type="speeding", location="Great North Rd",
                      speed_kmh=112.5, speed_limit_kmh=80, speed_device="  RADAR-LSK-07 ")
    assert created.status_code == 201, created.text

    listed = client.get(f"/api/enforcement/violations?vehicle_id={vid}", headers=h).json()
    row = next(v for v in listed if v["id"] == created.json()["id"])
    assert row["speed_kmh"] == 112.5
    assert row["speed_limit_kmh"] == 80
    assert row["speed_device"] == "RADAR-LSK-07"


def test_other_offences_dont_need_a_speed():
    h = _officer()
    vid = _vehicle(h, "EVD 704", "704011")
    r = _record(h, vehicle_id=vid, violation_type="no_insurance", location="Cairo Rd")
    assert r.status_code == 201, r.text


# -------------------------------- offence time --------------------------------

def test_offence_time_cannot_be_in_the_future():
    h = _officer()
    vid = _vehicle(h, "EVD 711", "711011")
    r = _record(h, vehicle_id=vid, violation_type="no_insurance", location="Cairo Rd",
                timestamp=_iso(timedelta(hours=2)))
    assert r.status_code == 422
    assert "future" in r.text


def test_a_phone_clock_slightly_ahead_is_tolerated():
    h = _officer()
    vid = _vehicle(h, "EVD 712", "712011")
    r = _record(h, vehicle_id=vid, violation_type="no_insurance", location="Cairo Rd",
                timestamp=_iso(timedelta(minutes=2)))
    assert r.status_code == 201, r.text


def test_same_day_backdating_needs_no_reason():
    h = _officer()
    vid = _vehicle(h, "EVD 713", "713011")
    r = _record(h, vehicle_id=vid, violation_type="no_insurance", location="Cairo Rd",
                timestamp=_iso(-timedelta(hours=3)))
    assert r.status_code == 201, r.text


def test_an_offence_days_old_needs_a_reason():
    h = _officer()
    vid = _vehicle(h, "EVD 714", "714011")
    three_days_ago = _iso(-timedelta(days=3))

    bare = _record(h, vehicle_id=vid, violation_type="reckless_driving", location="Kafue Rd",
                   timestamp=three_days_ago)
    assert bare.status_code == 422
    assert "more than a day old" in bare.text

    too_short = _record(h, vehicle_id=vid, violation_type="reckless_driving", location="Kafue Rd",
                        timestamp=three_days_ago, description="video")
    assert too_short.status_code == 422

    explained = _record(h, vehicle_id=vid, violation_type="reckless_driving", location="Kafue Rd",
                        timestamp=three_days_ago,
                        description="Citizen video posted on Facebook, plate clearly visible")
    assert explained.status_code == 201, explained.text


# ---------------------------------- GPS fix ----------------------------------

def test_gps_fix_is_stored_with_the_violation():
    h = _officer()
    vid = _vehicle(h, "EVD 721", "721011")
    created = _record(h, vehicle_id=vid, violation_type="illegal_parking", location="Cairo Rd",
                      latitude=-15.41667, longitude=28.28333, gps_accuracy_m=12)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["latitude"] == pytest.approx(-15.41667)
    assert body["longitude"] == pytest.approx(28.28333)
    assert body["gps_accuracy_m"] == 12


def test_half_a_gps_fix_is_rejected():
    h = _officer()
    vid = _vehicle(h, "EVD 722", "722011")
    r = _record(h, vehicle_id=vid, violation_type="illegal_parking", location="Cairo Rd", latitude=-15.4)
    assert r.status_code == 422
    assert "latitude and longitude" in r.text


def test_impossible_coordinates_are_rejected():
    h = _officer()
    vid = _vehicle(h, "EVD 723", "723011")
    r = _record(h, vehicle_id=vid, violation_type="illegal_parking", location="Cairo Rd",
                latitude=-154.1, longitude=28.28)
    assert r.status_code == 422


# ----------------------------- dashboard summary -----------------------------

def test_summary_counts_the_last_24_hours_in_total_and_by_me():
    me = _officer()
    colleague = _officer()
    vid = _vehicle(me, "EVD 731", "731011")

    before = client.get("/api/enforcement/violations/summary", headers=me).json()
    assert _record(me, vehicle_id=vid, violation_type="no_insurance", location="Cairo Rd").status_code == 201
    assert _record(colleague, vehicle_id=vid, violation_type="illegal_parking", location="Cairo Rd").status_code == 201
    after = client.get("/api/enforcement/violations/summary", headers=me).json()

    assert after["last_24h"] == before["last_24h"] + 2
    assert after["last_24h_by_me"] == before["last_24h_by_me"] + 1
