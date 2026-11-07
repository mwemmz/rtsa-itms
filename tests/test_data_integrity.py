"""Duplicate and invalid records are refused: the same car, chassis, NRC or permit cannot be added twice."""

import uuid

from fastapi.testclient import TestClient

from main import app
from tests.conftest import create_user, random_nrc

client = TestClient(app)
PW = "password123"


def _officer():
    email, _ = create_user("officer")
    token = client.post("/api/auth/login", json={"email": email, "password": PW}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _vehicle(**over):
    n = uuid.uuid4().hex[:6].upper()
    body = {"registration_number": f"ZZ {n}", "owner_name": "Owner", "owner_id_number": random_nrc(),
            "make": "Toyota", "model": "Hilux", "year": 2020, "chassis_number": f"CH{n}", "engine_number": f"EN{n}"}
    body.update(over)
    return body


def test_same_plate_cannot_be_registered_twice_however_it_is_typed():
    h = _officer()
    body = _vehicle()
    assert client.post("/api/vehicles/", json=body, headers=h).status_code == 201
    for variant in (body["registration_number"], body["registration_number"].lower(), body["registration_number"].replace(" ", ""),
                    "  " + body["registration_number"] + " "):
        other = _vehicle(registration_number=variant)
        assert client.post("/api/vehicles/", json=other, headers=h).status_code == 400, variant


def test_chassis_and_engine_numbers_are_unique():
    h = _officer()
    body = _vehicle()
    assert client.post("/api/vehicles/", json=body, headers=h).status_code == 201
    assert client.post("/api/vehicles/", json=_vehicle(chassis_number=body["chassis_number"].lower()), headers=h).status_code == 400
    assert client.post("/api/vehicles/", json=_vehicle(engine_number=body["engine_number"]), headers=h).status_code == 400


def test_bad_vehicle_values_are_refused():
    h = _officer()
    for over in ({"registration_number": ""}, {"registration_number": "<script>"}, {"registration_number": "A" * 60},
                 {"year": 1800}, {"year": 2999}):
        assert client.post("/api/vehicles/", json=_vehicle(**over), headers=h).status_code == 422, over


def _driver(**over):
    n = uuid.uuid4().hex[:6].upper()
    body = {"licence_number": f"L{n}", "first_name": "A", "last_name": "B", "id_number": random_nrc(), "date_of_birth": "1990-01-01T00:00:00",
            "licence_class": "B", "licence_issue_date": "2022-01-01T00:00:00", "licence_expiry_date": "2030-01-01T00:00:00"}
    body.update(over)
    return body


def test_driver_rules():
    h = _officer()
    d = _driver()
    assert client.post("/api/drivers/", json=d, headers=h).status_code == 201
    assert client.post("/api/drivers/", json=_driver(licence_number=d["licence_number"]), headers=h).status_code == 400
    assert client.post("/api/drivers/", json=_driver(id_number=d["id_number"]), headers=h).status_code == 400
    assert client.post("/api/drivers/", json=_driver(licence_expiry_date="2020-01-01T00:00:00"), headers=h).status_code == 422
    assert client.post("/api/drivers/", json=_driver(date_of_birth="2999-01-01T00:00:00"), headers=h).status_code == 422
    assert client.post("/api/drivers/", json=_driver(date_of_birth="2020-01-01T00:00:00"), headers=h).status_code == 422


def test_insurance_end_must_follow_start_and_renewal_keeps_current_cover():
    h = _officer()
    v = client.post("/api/vehicles/", json=_vehicle(), headers=h).json()
    base = {"vehicle_id": v["id"], "provider": "ZSIC"}
    assert client.post("/api/insurance/", json={**base, "policy_number": "P-" + uuid.uuid4().hex[:8],
                       "start_date": "2999-01-01T00:00:00", "end_date": "2998-01-01T00:00:00"}, headers=h).status_code == 422
    assert client.post("/api/insurance/", json={**base, "policy_number": "P-" + uuid.uuid4().hex[:8],
                       "start_date": "2020-01-01T00:00:00", "end_date": "2999-01-01T00:00:00"}, headers=h).status_code == 201
    # a later-dated policy that does not overlap must not switch off today's cover
    assert client.post("/api/insurance/", json={**base, "policy_number": "P-" + uuid.uuid4().hex[:8],
                       "start_date": "3000-01-01T00:00:00", "end_date": "3001-01-01T00:00:00"}, headers=h).status_code == 201
    assert client.get(f"/api/insurance/vehicle/{v['id']}/active", headers=h).status_code == 200


def test_psv_permit_rules():
    h = _officer()
    v = client.post("/api/vehicles/", json=_vehicle(), headers=h).json()
    op = client.post("/api/psv/operators", json={"name": "Op", "licence_number": "OP-" + uuid.uuid4().hex[:6], "contact_person": "X", "phone": "0977000000"}, headers=h).json()
    permit = {"operator_id": op["id"], "vehicle_id": v["id"], "permit_number": "PM-" + uuid.uuid4().hex[:6], "route": "A to B",
              "issued_date": "2026-01-01T00:00:00", "expiry_date": "2030-01-01T00:00:00"}
    assert client.post("/api/psv/permits", json=permit, headers=h).status_code == 201
    assert client.post("/api/psv/permits", json={**permit, "permit_number": permit["permit_number"] + "X"}, headers=h).status_code == 400  # overlapping
    assert client.post("/api/psv/permits", json={**permit, "permit_number": "PM-" + uuid.uuid4().hex[:6],
                       "issued_date": "2031-01-01T00:00:00", "expiry_date": "2030-06-01T00:00:00"}, headers=h).status_code == 422


def test_anpr_confidence_must_be_between_zero_and_one():
    from app.schemas.anpr import ANPREventCreate
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ANPREventCreate(plate_number="BAL 1234", location="Great East Rd", confidence=7)
    assert ANPREventCreate(plate_number="BAL 1234", location="Great East Rd", confidence=0.9).confidence == 0.9
