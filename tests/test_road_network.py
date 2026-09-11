import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"

from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402
import app.models  # noqa: F401, E402
import app.core.ratelimit as ratelimit  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.driver import Driver, LicenceClass  # noqa: E402
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType  # noqa: E402
from app.models.notification import NotificationRule  # noqa: E402
from app.models.vehicle import Vehicle  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


@pytest.fixture(autouse=True)
def _seed_rules():
    from scripts.seed import DEFAULT_RULES

    db = SessionLocal()
    try:
        existing = {r.trigger_event for r in db.query(NotificationRule).all()}
        for rule_data in DEFAULT_RULES:
            if rule_data["trigger_event"] not in existing:
                db.add(NotificationRule(**rule_data))
        db.commit()
    finally:
        db.close()


def _make_user(role: str = "admin"):
    email = f"{role}_{uuid4().hex[:8]}@test.com"
    client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "full_name": "Road Tester", "role": role},
    )
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _build_network(headers):
    r1 = client.post(
        "/api/road-network/roads",
        json={"name": f"Route {uuid4().hex[:6]}", "road_class": "main_road"},
        headers=headers,
    ).json()
    r2 = client.post(
        "/api/road-network/roads",
        json={"name": f"Detour {uuid4().hex[:6]}", "road_class": "secondary"},
        headers=headers,
    ).json()
    a = client.post(
        "/api/road-network/intersections",
        json={"name": f"Alpha {uuid4().hex[:4]}", "latitude": -15.41, "longitude": 28.28},
        headers=headers,
    ).json()
    b = client.post(
        "/api/road-network/intersections",
        json={"name": f"Beta {uuid4().hex[:4]}", "latitude": -15.40, "longitude": 28.29},
        headers=headers,
    ).json()
    c = client.post(
        "/api/road-network/intersections",
        json={"name": f"Gamma {uuid4().hex[:4]}", "latitude": -15.405, "longitude": 28.30},
        headers=headers,
    ).json()
    ab = client.post(
        "/api/road-network/segments",
        json={"road_id": r1["id"], "start_intersection_id": a["id"], "end_intersection_id": b["id"],
              "distance_km": 2.0, "travel_minutes": 2.0},
        headers=headers,
    ).json()
    ac = client.post(
        "/api/road-network/segments",
        json={"road_id": r2["id"], "start_intersection_id": a["id"], "end_intersection_id": c["id"],
              "distance_km": 3.0, "travel_minutes": 4.0},
        headers=headers,
    ).json()
    cb = client.post(
        "/api/road-network/segments",
        json={"road_id": r2["id"], "start_intersection_id": c["id"], "end_intersection_id": b["id"],
              "distance_km": 3.0, "travel_minutes": 4.0},
        headers=headers,
    ).json()
    return {"alpha": a, "beta": b, "gamma": c, "r1": r1, "ab": ab, "ac": ac, "cb": cb}


def test_route_planner_takes_direct_and_reroutes_around_incident():
    officer = _make_user("officer")
    citizen = _make_user("citizen")
    net = _build_network(officer)

    # Direct route Alpha -> Beta via the fast segment
    r = client.get(
        "/api/routing/route",
        params={"from_": net["alpha"]["name"], "to": net["beta"]["name"]},
        headers=citizen,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["primary_route"]["total_minutes"] == 2.0
    assert [s["road_name"] for s in body["primary_route"]["steps"]] == [net["r1"]["name"]]

    # Report an accident blocking the fast segment
    r = client.post(
        "/api/incidents/",
        json={
            "incident_type": "accident",
            "severity": "serious",
            "segment_id": net["ab"]["id"],
            "road_id": net["r1"]["id"],
            "description": "Two trucks collided.",
            "broadcast_alert": True,
        },
        headers=officer,
    )
    assert r.status_code == 201, r.text

    # Routing now avoids Alpha->Beta and returns the detour via Gamma
    r = client.get(
        "/api/routing/route",
        params={"from_": net["alpha"]["name"], "to": net["beta"]["name"]},
        headers=citizen,
    )
    body = r.json()
    assert body["incidents_avoided"] >= 1
    steps = body["primary_route"]["steps"]
    assert steps[0]["to_intersection"] == net["gamma"]["name"]
    assert body["primary_route"]["total_minutes"] == 8.0

    # A live alert with a reroute hint is available to any motorist
    alerts = client.get("/api/incidents/alerts", headers=citizen).json()
    assert any(net["r1"]["name"] in (a.get("road") or "") for a in alerts)
    assert any("alternative" in (a.get("suggestion") or "").lower() for a in alerts)

    # Status board and leaflet reflect the closure
    status = client.get("/api/routing/status", headers=citizen).json()
    closed = [s for s in status if net["r1"]["name"] in s["road"]]
    assert closed and closed[0]["status"] == "closed"

    leaflet = client.get("/api/road-network/leaflet", headers=citizen).json()
    assert any(net["r1"]["name"] in e["road"] and e["active_incidents"] == 1 for e in leaflet["roads"])

    geo = client.get("/api/road-network/geojson", headers=citizen).json()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) >= 3


def test_incident_broadcasts_alert_to_all_users():
    _make_user("citizen")
    officer = _make_user("officer")
    net = _build_network(officer)

    client.post(
        "/api/incidents/",
        json={
            "incident_type": "road_closed",
            "severity": "fatal",
            "segment_id": net["ac"]["id"],
            "road_id": net["r1"]["id"],
            "description": "Bridge out.",
            "broadcast_alert": True,
        },
        headers=officer,
    )

    db = SessionLocal()
    try:
        count = (
            db.query(NotificationRule)
            .filter(NotificationRule.trigger_event == "road_alert")
            .count()
        )
        assert count == 1
        from app.models.notification import Notification

        alerts = db.query(Notification).filter(Notification.trigger_event == "road_alert").all()
        assert len(alerts) >= 2  # citizen + officer both got the alert
    finally:
        db.close()


def test_driver_portal_flow():
    from app.models.user import User, UserRole

    token_headers = None
    citizen_email = None
    # Register a citizen and link a licence + vehicle (as an officer would)
    resp = client.post(
        "/api/auth/register",
        json={
            "email": f"portal_{uuid4().hex[:8]}@test.com",
            "password": "password123",
            "full_name": "Portal Driver",
            "role": UserRole.CITIZEN.value,
        },
    )
    assert resp.status_code == 201
    citizen_email = resp.json()["email"]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == citizen_email).first()
        assert user is not None
        user.id = user.id

        driver = Driver(
            user_id=user.id,
            licence_number=f"LIC-PTL-{uuid4().hex[:8]}",
            first_name="Portal",
            last_name="Driver",
            id_number="778899",
            date_of_birth=datetime(1990, 5, 5),
            phone_number="+260977000001",
            email=citizen_email,
            licence_class=LicenceClass.B,
            licence_issue_date=datetime.utcnow() - timedelta(days=200),
            licence_expiry_date=datetime.utcnow() + timedelta(days=20),  # expiring soon
        )
        db.add(driver)

        vehicle = Vehicle(
            user_id=user.id,
            registration_number=f"PTL {uuid4().hex[:4]}",
            owner_name="Portal Driver",
            owner_id_number="778899",
            make="Toyota",
            model="Corolla",
            year=2020,
        )
        db.add(vehicle)
        db.flush()

        violation = Violation(
            vehicle_id=vehicle.id,
            violation_type=ViolationType.SPEEDING,
            location="Mwembeshi Road",
            timestamp=datetime.utcnow() - timedelta(days=3),
        )
        db.add(violation)
        db.flush()
        challan = Challan(
            reference=f"CH-PTL-{uuid4().hex[:6]}",
            violation_id=violation.id,
            vehicle_id=vehicle.id,
            penalty_amount=500,
            due_date=datetime.utcnow() + timedelta(days=10),
            status=ChallanStatus.UNPAID,
        )
        db.add(challan)
        db.commit()
        vehicle_id = str(vehicle.id)
        challan_id = str(challan.id)
    finally:
        db.close()

    login = client.post(
        "/api/auth/login", json={"email": citizen_email, "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Licence shows as expiring soon
    licence = client.get("/api/portal/licence", headers=headers).json()
    assert licence["state"] == "expiring_soon"
    assert licence["days_until_expiry"] == 20

    # Dashboard surfaces the fine and the vehicle
    dash = client.get("/api/portal/dashboard", headers=headers).json()
    assert len(dash["vehicles"]) == 1
    assert dash["total_outstanding"] == 500
    assert len(dash["outstanding_fines"]) == 1

    # Pay the fine
    paid = client.post(f"/api/portal/fines/{challan_id}/pay", headers=headers).json()
    assert paid["status"] == "paid"
    after = client.get("/api/portal/fines", headers=headers).json()
    assert after == []

    # Renew the licence (sandbox payment)
    renewed = client.post("/api/portal/licence/renew", headers=headers).json()
    assert renewed["state"] == "valid"
    assert renewed["days_until_expiry"] > 1800
    assert renewed["status"] == "active"
    del vehicle, challan  # satisfy linters about unused loop vars


def test_worker_licence_expiry_scan():
    from app.models.user import User, UserRole
    from app.workers.notification_worker import scan_licence_expiries

    resp = client.post(
        "/api/auth/register",
        json={
            "email": f"expiry_{uuid4().hex[:8]}@test.com",
            "password": "password123",
            "full_name": "Expiring Driver",
            "role": UserRole.CITIZEN.value,
        },
    )
    assert resp.status_code == 201

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == resp.json()["email"]).first()
        driver = Driver(
            user_id=user.id,
            licence_number=f"LIC-EXP-{uuid4().hex[:8]}",
            first_name="Expiring",
            last_name="Driver",
            id_number="334455",
            date_of_birth=datetime(1988, 3, 3),
            email=resp.json()["email"],
            licence_class=LicenceClass.B,
            licence_issue_date=datetime.utcnow() - timedelta(days=400),
            licence_expiry_date=datetime.utcnow() + timedelta(days=25),
        )
        db.add(driver)
        db.commit()
    finally:
        db.close()

    sent = scan_licence_expiries()
    assert sent >= 1

    db = SessionLocal()
    try:
        from app.models.notification import Notification

        created = (
            db.query(Notification)
            .filter(Notification.trigger_event == "licence_expiring")
            .count()
        )
        assert created >= 1
    finally:
        db.close()