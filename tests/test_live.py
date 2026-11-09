"""Live monitoring: staff-only feed of simulated vehicle positions."""
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.core.database import SessionLocal
from app.services import live_fleet
from main import app
from tests.conftest import create_user

client = TestClient(app)
PW = "password123"


def _headers(role):
    email, _ = create_user(role)
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_live_is_staff_only():
    assert client.get("/api/live/vehicles").status_code in (401, 403)
    assert client.get("/api/live/vehicles", headers=_headers("citizen")).status_code == 403
    assert client.get("/api/live/vehicles", headers=_headers("officer")).status_code == 200


def test_live_shape_and_movement():
    body = client.get("/api/live/vehicles", headers=_headers("admin")).json()
    assert body["simulated"] is True and set(body["summary"]) >= {"total", "moving", "flagged", "off_route"}
    db = SessionLocal()
    try:
        a, b = live_fleet.snapshot(db, 1000.0), live_fleet.snapshot(db, 1010.0)
    finally:
        db.close()
    for v in a:
        assert -90 <= v["lat"] <= 90 and 0 <= v["heading"] < 360 and v["state"] in ("ok", "flagged", "offroute")
    moved = [(x["lat"], x["lon"]) != (y["lat"], y["lon"]) for x, y in zip(a, b) if x["moving"] and y["moving"]]
    assert not moved or any(moved)
