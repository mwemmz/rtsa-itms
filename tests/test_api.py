import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"

from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402
import app.models  # noqa: F401, E402  # register all models on Base.metadata
import app.core.ratelimit as ratelimit  # noqa: E402
from app.core.database import Base, engine  # noqa: E402

Base.metadata.create_all(bind=engine)

from app.models.user import UserRole  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _register_and_login(role: UserRole = UserRole.ADMIN) -> str:
    email = f"{role.value}_{uuid4().hex[:8]}@test.com"
    payload = {"email": email, "password": "password123", "full_name": "Test User", "role": role.value}
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_register_and_login():
    token = _register_and_login(UserRole.CITIZEN)
    assert token


def test_vehicle_crud():
    token = _register_and_login()
    headers = _auth(token)
    resp = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TEST 100",
            "owner_name": "Test Owner",
            "owner_id_number": "100011",
            "make": "Toyota",
            "model": "Corolla",
            "year": 2021,
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    vehicle_id = resp.json()["id"]

    resp = client.get(f"/api/vehicles/{vehicle_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["registration_number"] == "TEST 100"

    resp = client.patch(
        f"/api/vehicles/{vehicle_id}",
        json={"color": "Blue"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["color"] == "Blue"


def test_vehicle_lookup_by_registration():
    token = _register_and_login()
    headers = _auth(token)
    resp = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TEST 101",
            "owner_name": "Test Owner",
            "owner_id_number": "100011",
            "make": "Toyota",
            "model": "Corolla",
            "year": 2021,
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201)

    resp = client.get("/api/vehicles/by-registration/TEST 101", headers=headers)
    assert resp.status_code == 200, resp.text


def test_login_rate_limit():
    for _ in range(6):
        resp = client.post(
            "/api/auth/login",
            json={"email": "nobody@test.com", "password": "wrong"},
        )
    assert resp.status_code == 429


def test_driver_crud():
    token = _register_and_login()
    headers = _auth(token)
    from datetime import datetime
    resp = client.post(
        "/api/drivers/",
        json={
            "licence_number": "LIC-TEST-001",
            "first_name": "John",
            "last_name": "Mwale",
            "id_number": "112233",
            "date_of_birth": datetime(1990, 1, 1).isoformat(),
            "licence_class": "B",
            "licence_issue_date": datetime(2024, 1, 1).isoformat(),
            "licence_expiry_date": datetime(2029, 1, 1).isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    driver_id = resp.json()["id"]

    resp = client.get(f"/api/drivers/{driver_id}", headers=headers)
    assert resp.status_code == 200


def test_challan_generation_flow():
    token = _register_and_login()
    headers = _auth(token)
    resp = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TEST 200",
            "owner_name": "Challan Owner",
            "owner_id_number": "200011",
            "make": "Nissan",
            "model": "Navara",
            "year": 2015,
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201)
    vehicle_id = resp.json()["id"]

    resp = client.post(
        "/api/enforcement/violations",
        json={
            "vehicle_id": vehicle_id,
            "violation_type": "no_insurance",
            "location": "Great East Road",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text

    challans = client.get("/api/enforcement/challans", headers=headers).json()
    assert len(challans) >= 1
    first = challans[0]
    assert first["reference"].startswith("CH-")


def test_toll_compliance_flow():
    token = _register_and_login()
    headers = _auth(token)
    resp = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TEST 300",
            "owner_name": "Toll Owner",
            "owner_id_number": "300011",
            "make": "BMW",
            "model": "X5",
            "year": 2019,
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201)
    vehicle_id = resp.json()["id"]

    # No insurance/fitness -> should be flagged
    resp = client.post(
        "/api/toll/events",
        json={"plate_number": "TEST 300", "gate_id": "gate-001", "toll_amount": 500},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["compliance_result"] == "flagged"
    assert body["challan_created"] is True


def test_payment_flow():
    token = _register_and_login()
    headers = _auth(token)
    resp = client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TEST 400",
            "owner_name": "Pay Owner",
            "owner_id_number": "400011",
            "make": "Ford",
            "model": "Ranger",
            "year": 2018,
        },
        headers=headers,
    )
    vehicle_id = resp.json()["id"]
    client.post(
        "/api/enforcement/violations",
        json={"vehicle_id": vehicle_id, "violation_type": "speeding", "location": "Kafue Road"},
        headers=headers,
    )
    challan = client.get("/api/enforcement/challans", headers=headers).json()[0]

    resp = client.post(
        "/api/payments/",
        json={
            "payment_type": "fine",
            "related_entity_id": challan["id"],
            "amount": challan["penalty_amount"],
            "gateway": "sandbox",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["status"] == "completed"


def test_audit_logged():
    token = _register_and_login()
    headers = _auth(token)
    client.post(
        "/api/vehicles/",
        json={
            "registration_number": "TEST 500",
            "owner_name": "Audit Owner",
            "owner_id_number": "500011",
            "make": "Honda",
            "model": "Civic",
            "year": 2020,
        },
        headers=headers,
    )
    logs = client.get("/api/admin/audit-logs", headers=headers).json()
    assert logs, "audit logs should be non-empty"
    assert any(log["entity_type"] == "vehicle" for log in logs)