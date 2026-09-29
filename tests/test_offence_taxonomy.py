"""Driver / vehicle offence taxonomy.

Offences split along two axes RTSA treats differently: driver offences attach to
the person behind the wheel and carry licence consequences, vehicle offences
attach to the registered owner and can ground impoundment, and some implicate
both.
"""

import os
import sys
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

from app.models.enforcement import (  # noqa: E402
    BOTH_OFFENCES,
    DRIVER_OFFENCES,
    VEHICLE_OFFENCES,
    Violation,
    ViolationType,
)
from app.models.user import UserRole  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _register_and_login(role: UserRole = UserRole.OFFICER) -> str:
    from tests.conftest import create_user

    email, _ = create_user(role.value)
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_speeding_and_red_light_are_driver_offences():
    assert ViolationType.SPEEDING.category == "driver"
    assert ViolationType.RUNNING_RED_LIGHT.category == "driver"
    assert ViolationType.DRUNK_DRIVING.category == "driver"
    assert ViolationType.RECKLESS_DRIVING.category == "driver"
    assert ViolationType.USING_PHONE.category == "driver"
    assert ViolationType.DRIVING_WITHOUT_LICENCE.category == "driver"


def test_vehicle_condition_and_documentation_are_vehicle_offences():
    assert ViolationType.UNROADWORTHY.category == "vehicle"
    assert ViolationType.EXPIRED_ROAD_TAX.category == "vehicle"
    assert ViolationType.MISSING_NUMBER_PLATES.category == "vehicle"
    assert ViolationType.ILLEGAL_MODIFICATION.category == "vehicle"
    assert ViolationType.OVERLOADING.category == "vehicle"
    assert ViolationType.EXPIRED_FITNESS.category == "vehicle"


def test_insurance_and_parking_impicate_both_parties():
    assert ViolationType.NO_INSURANCE.category == "both"
    assert ViolationType.ILLEGAL_PARKING.category == "both"


def test_every_offence_falls_in_exactly_one_category():
    classified = DRIVER_OFFENCES | VEHICLE_OFFENCES | BOTH_OFFENCES
    assert classified == set(ViolationType)
    assert not (DRIVER_OFFENCES & VEHICLE_OFFENCES)
    assert not (DRIVER_OFFENCES & BOTH_OFFENCES)
    assert not (VEHICLE_OFFENCES & BOTH_OFFENCES)


def test_only_driver_offences_carry_licence_consequence():
    for offence in ViolationType:
        assert offence.carries_licence_consequence is (offence in DRIVER_OFFENCES)


def test_only_vehicle_offences_ground_impoundment():
    for offence in ViolationType:
        assert offence.grounds_impoundment is (offence in VEHICLE_OFFENCES)


def test_liability_falls_on_owner_for_vehicle_offences():
    assert ViolationType.EXPIRED_ROAD_TAX.liable_party == "owner"
    assert ViolationType.SPEEDING.liable_party == "driver"
    assert ViolationType.NO_INSURANCE.liable_party == "both"


def test_violation_model_exposes_taxonomy():
    violation = Violation(violation_type=ViolationType.UNROADWORTHY, location="Lumana Road")
    assert violation.category == "vehicle"
    assert violation.grounds_impoundment is True
    assert violation.carries_licence_consequence is False


def test_api_records_liability_and_consequences():
    headers = _auth(_register_and_login())
    vehicle = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TAX 001",
            "owner_name": "Taxonomy Owner",
            "owner_id_number": "300011",
            "make": "Toyota",
            "model": "Hilux",
            "year": 2019,
        },
        headers=headers,
    ).json()

    created = client.post(
        "/api/enforcement/violations",
        json={
            "vehicle_id": vehicle["id"],
            "violation_type": "speeding",
            "location": "Great East Road",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["category"] == "driver"
    assert body["liable_party"] == "driver"
    assert body["carries_licence_consequence"] is True
    assert body["grounds_impoundment"] is False


def test_challan_inherits_category_from_its_violation():
    headers = _auth(_register_and_login())
    vehicle = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TAX 002",
            "owner_name": "Roadworthy Owner",
            "owner_id_number": "300022",
            "make": "Hino",
            "model": "300",
            "year": 2018,
        },
        headers=headers,
    ).json()

    client.post(
        "/api/enforcement/violations",
        json={
            "vehicle_id": vehicle["id"],
            "violation_type": "unroadworthy",
            "location": "Chipata Road",
        },
        headers=headers,
    )

    challans = client.get("/api/enforcement/challans?limit=100", headers=headers).json()
    mine = [c for c in challans if c.get("violation_type") == "unroadworthy"]
    assert mine, "expected an unroadworthy challan"
    assert mine[0]["category"] == "vehicle"
    assert mine[0]["liable_party"] == "owner"
    assert mine[0]["grounds_impoundment"] is True
    assert mine[0]["carries_licence_consequence"] is False
