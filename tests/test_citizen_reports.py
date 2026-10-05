"""Citizen road reports: NRC at sign-up, live effect on routing, officer review, false-report fines."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as ratelimit
from app.core.database import SessionLocal
from app.models.accident import Accident
from app.models.enforcement import Challan
from app.models.notification import Notification, NotificationRule
from app.services import settings as runtime_settings
from main import app
from tests.conftest import create_user, random_nrc

client = TestClient(app)
PW = "password123"


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


@pytest.fixture
def setting():
    """Override runtime settings for one test, restored afterwards."""
    changed = []

    def _set(key, value):
        db = SessionLocal()
        try:
            runtime_settings.set_value(db, key, value, None)
            db.commit()
        finally:
            db.close()
        changed.append(key)

    yield _set
    db = SessionLocal()
    try:
        for key in changed:
            runtime_settings.reset(db, key)
        db.commit()
    finally:
        db.close()


def _login(email):
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user(role="citizen", **extra):
    if role == "citizen":
        extra.setdefault("nrc_number", random_nrc())
    email, uid = create_user(role, **extra)
    return _login(email), uid


def _network(officer):
    """Alpha -(fast road, 2 min)- Beta, and a slower detour Alpha - Gamma - Beta."""
    def post(path, body):
        r = client.post(path, json=body, headers=officer)
        assert r.status_code in (200, 201), r.text
        return r.json()

    tag = uuid4().hex[:6]
    fast = post("/api/road-network/roads", {"name": f"Fast {tag}", "road_class": "main_road"})
    slow = post("/api/road-network/roads", {"name": f"Slow {tag}", "road_class": "secondary"})
    a = post("/api/road-network/intersections", {"name": f"Alpha {tag}", "latitude": -15.41, "longitude": 28.28})
    b = post("/api/road-network/intersections", {"name": f"Beta {tag}", "latitude": -15.40, "longitude": 28.29})
    c = post("/api/road-network/intersections", {"name": f"Gamma {tag}", "latitude": -15.405, "longitude": 28.30})
    seg = lambda road, x, y, mins: post("/api/road-network/segments", {  # noqa: E731
        "road_id": road["id"], "start_intersection_id": x["id"], "end_intersection_id": y["id"],
        "distance_km": mins, "travel_minutes": mins})
    return {"a": a, "b": b, "ab": seg(fast, a, b, 2.0), "ac": seg(slow, a, c, 4.0), "cb": seg(slow, c, b, 4.0)}


def _report(headers, segment_id, **body):
    payload = {"incident_type": "accident", "severity": "serious", "segment_id": segment_id,
               "description": "Two cars collided", "declaration": True}
    payload.update(body)
    return client.post("/api/incidents/", json=payload, headers=headers)


def _route_minutes(headers, net):
    r = client.get("/api/routing/route", params={"from_": net["a"]["name"], "to": net["b"]["name"],
                                                 "include_alternatives": False}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["primary_route"]["total_minutes"]


def _register(**body):
    payload = {"email": f"nrc{uuid4().hex[:8]}@t.com", "password": PW, "full_name": "Mutale Phiri"}
    payload.update(body)
    return client.post("/api/auth/register", json=payload,
                       headers={"X-Forwarded-For": f"10.77.{uuid4().int % 250}.1"})


# --- NRC at sign-up -------------------------------------------------------------------------

def test_sign_up_requires_a_valid_unique_nrc():
    assert _register().status_code == 400  # no NRC
    assert "NRC" in _register(nrc_number="12345/67/8").json()["detail"]

    nrc = random_nrc()
    r = _register(nrc_number=nrc.replace("/", ""))  # typed without slashes
    assert r.status_code == 201, r.text
    assert r.json()["nrc_number"] == nrc  # stored in the card's format

    again = _register(nrc_number=nrc)
    assert again.status_code == 400 and "already uses this NRC" in again.json()["detail"]


def test_an_older_account_can_add_its_nrc_once_but_not_change_it():
    headers, _ = _user("citizen", nrc_number=None)
    status = client.get("/api/incidents/reporting-status", headers=headers).json()
    assert status["can_report"] is False and status["nrc_on_file"] is False

    nrc = random_nrc()
    r = client.patch("/api/citizen/profile", json={"nrc_number": nrc}, headers=headers)
    assert r.status_code == 200 and r.json()["nrc_number"] == nrc
    assert client.get("/api/incidents/reporting-status", headers=headers).json()["can_report"] is True

    r = client.patch("/api/citizen/profile", json={"nrc_number": random_nrc()}, headers=headers)
    assert r.status_code == 422 and "only be changed by RTSA" in r.json()["detail"]


# --- reporting ------------------------------------------------------------------------------

def test_citizen_report_reroutes_everyone_at_once_and_waits_for_review():
    officer, _ = _user("officer")
    citizen, citizen_id = _user("citizen")
    other, _ = _user("citizen")
    net = _network(officer)
    assert _route_minutes(other, net) == 2.0

    r = _report(citizen, net["ab"]["id"])
    assert r.status_code == 201, r.text
    report = r.json()
    assert report["verification"] == "unverified" and report["is_active"] is True

    # The stretch is closed in everyone's route planner straight away.
    assert _route_minutes(other, net) == 8.0
    assert _route_minutes(officer, net) == 8.0

    # Everyone sees it flagged as unverified; only officers see who reported it.
    seen = next(a for a in client.get("/api/incidents/alerts", headers=other).json() if a["id"] == report["id"])
    assert seen["verification"] == "unverified" and seen["blocking"] is True
    assert seen["mine"] is False and "reporter" not in seen
    mine = next(a for a in client.get("/api/incidents/alerts", headers=citizen).json() if a["id"] == report["id"])
    assert mine["mine"] is True
    staff_view = next(a for a in client.get("/api/incidents/alerts", headers=officer).json() if a["id"] == report["id"])
    assert staff_view["reporter"]["nrc"] and staff_view["reporter"]["false_reports"] == 0
    portal = next(a for a in client.get("/api/portal/alerts", headers=other).json() if a["id"] == report["id"])
    assert portal["verification"] == "unverified"

    # Officers are told there is something to review; motorists aren't pushed an unverified alert.
    db = SessionLocal()
    try:
        notes = db.query(Notification).filter(Notification.trigger_event == "road_report_received").all()
        assert notes and all(n.user_id != citizen_id for n in notes)
    finally:
        db.close()

    listed = client.get("/api/incidents/mine", headers=citizen).json()
    assert listed[0]["id"] == report["id"] and listed[0]["road_name"].startswith("Fast")


def test_citizen_report_needs_a_declaration_a_stretch_and_no_duplicate():
    officer, _ = _user("officer")
    citizen, _ = _user("citizen")
    net = _network(officer)

    assert _report(citizen, net["ab"]["id"], declaration=False).status_code == 422
    whole_road = _report(citizen, None, road_id=net["ab"]["road_id"])
    assert whole_road.status_code == 422 and "stretch" in whole_road.json()["detail"]

    assert _report(citizen, net["ab"]["id"]).status_code == 201
    dup = _report(_user("citizen")[0], net["ab"]["id"])
    assert dup.status_code == 409


def test_toll_operators_cannot_report_and_citizens_cannot_review():
    officer, _ = _user("officer")
    citizen, _ = _user("citizen")
    toll, _ = _user("toll_operator")
    net = _network(officer)
    assert _report(toll, net["ab"]["id"]).status_code == 403
    report = _report(citizen, net["ab"]["id"]).json()
    assert client.post(f"/api/incidents/{report['id']}/confirm", headers=citizen).status_code == 403
    assert client.post(f"/api/incidents/{report['id']}/dismiss", json={"reason": "not there", "false_report": True},
                       headers=citizen).status_code == 403


def test_too_many_open_reports_blocks_more_until_reviewed(setting):
    setting("road_reports.max_open_reports", 1)
    officer, _ = _user("officer")
    citizen, _ = _user("citizen")
    net = _network(officer)
    first = _report(citizen, net["ab"]["id"]).json()
    blocked = _report(citizen, net["ac"]["id"])
    assert blocked.status_code == 403 and "waiting for an officer" in blocked.json()["detail"]

    client.post(f"/api/incidents/{first['id']}/confirm", headers=officer)
    assert _report(citizen, net["ac"]["id"]).status_code == 201


# --- officer review -------------------------------------------------------------------------

def test_confirming_an_accident_opens_a_case_and_alerts_motorists():
    officer, _ = _user("officer")
    citizen, citizen_id = _user("citizen")
    net = _network(officer)
    report = _report(citizen, net["ab"]["id"]).json()

    assert client.post(f"/api/incidents/{report['id']}/resolve", headers=officer).status_code == 409

    r = client.post(f"/api/incidents/{report['id']}/confirm", headers=officer)
    assert r.status_code == 200, r.text
    assert r.json()["verification"] == "confirmed" and r.json()["is_active"] is True
    assert client.post(f"/api/incidents/{report['id']}/confirm", headers=officer).status_code == 409

    db = SessionLocal()
    try:
        accident = db.query(Accident).filter(Accident.incident_id == report["id"]).one()
        assert accident.reported_by == citizen_id
        assert db.query(Notification).filter(Notification.user_id == citizen_id,
                                             Notification.trigger_event == "road_report_confirmed").count() == 1
        accident_id = str(accident.id)
    finally:
        db.close()

    case = next(a for a in client.get("/api/accidents/", headers=officer).json() if a["id"] == accident_id)
    assert case["on_road"] is True
    # Clearing the scene from the Accidents screen reopens the stretch.
    assert client.post(f"/api/accidents/{accident_id}/clear-road", headers=officer).status_code == 200
    assert _route_minutes(citizen, net) == 2.0


def test_dismissing_a_report_reopens_the_road_without_blame():
    officer, _ = _user("officer")
    citizen, citizen_id = _user("citizen")
    net = _network(officer)
    report = _report(citizen, net["ab"]["id"]).json()

    r = client.post(f"/api/incidents/{report['id']}/dismiss",
                    json={"false_report": False, "reason": "Scene already cleared"}, headers=officer)
    assert r.status_code == 200, r.text
    assert r.json()["verification"] == "dismissed" and r.json()["is_active"] is False
    assert _route_minutes(citizen, net) == 2.0

    mine = client.get("/api/incidents/mine", headers=citizen).json()[0]
    assert mine["verification"] == "dismissed" and mine["review_note"] == "Scene already cleared"
    db = SessionLocal()
    try:
        assert db.query(Challan).filter(Challan.user_id == citizen_id).count() == 0
    finally:
        db.close()
    assert client.get("/api/incidents/reporting-status", headers=citizen).json()["false_reports"] == 0


def test_a_false_report_fines_the_reporter_who_can_pay_it(setting):
    setting("road_reports.false_report_fine", 250000)
    officer, _ = _user("officer")
    citizen, citizen_id = _user("citizen")
    bystander, _ = _user("citizen")  # no vehicles either: must not see someone else's fine
    net = _network(officer)
    report = _report(citizen, net["ab"]["id"]).json()

    reason_too_short = client.post(f"/api/incidents/{report['id']}/dismiss",
                                   json={"false_report": True, "reason": "no"}, headers=officer)
    assert reason_too_short.status_code == 422

    r = client.post(f"/api/incidents/{report['id']}/dismiss",
                    json={"false_report": True, "reason": "No accident at the scene; road clear"}, headers=officer)
    assert r.status_code == 200, r.text
    assert r.json()["verification"] == "false"
    assert _route_minutes(citizen, net) == 2.0

    fines = client.get("/api/portal/fines", headers=citizen).json()
    assert len(fines) == 1
    fine = fines[0]
    assert fine["violation_type"] == "false_report" and fine["category"] == "reporter"
    assert fine["penalty_amount"] == 250000
    assert client.get("/api/portal/fines", headers=bystander).json() == []
    assert client.get("/api/citizen/my-fines", headers=bystander).json() == []

    # Officers see who is charged on the challan list.
    listed = next(c for c in client.get("/api/enforcement/challans", headers=officer).json() if c["id"] == fine["id"])
    assert listed["liable_party"] == "reporter" and "NRC" in listed["account_name"]

    db = SessionLocal()
    try:
        note = db.query(Notification).filter(Notification.user_id == citizen_id,
                                             Notification.trigger_event == "road_report_false").first()
        assert note is not None and fine["reference"] in note.body
    finally:
        db.close()

    assert client.post(f"/api/portal/fines/{fine['id']}/pay", headers=bystander).status_code == 403
    paid = client.post(f"/api/portal/fines/{fine['id']}/pay", headers=citizen)
    assert paid.status_code == 200, paid.text
    assert client.get("/api/portal/fines", headers=citizen).json() == []


def test_repeated_false_reports_suspend_reporting(setting):
    setting("road_reports.strike_limit", 2)
    officer, _ = _user("officer")
    citizen, _ = _user("citizen")
    net = _network(officer)
    for seg in ("ab", "ac"):
        report = _report(citizen, net[seg]["id"]).json()
        r = client.post(f"/api/incidents/{report['id']}/dismiss",
                        json={"false_report": True, "reason": "Nothing there on arrival"}, headers=officer)
        assert r.status_code == 200, r.text

    status = client.get("/api/incidents/reporting-status", headers=citizen).json()
    assert status["can_report"] is False and status["false_reports"] == 2
    assert status["suspended_until"] is not None
    blocked = _report(citizen, net["cb"]["id"])
    assert blocked.status_code == 403 and "suspended" in blocked.json()["detail"]


def test_false_report_fines_come_only_from_review_not_the_violation_form():
    officer, _ = _user("officer")
    r = client.post("/api/enforcement/violations", json={
        "violation_type": "false_report", "location": "Cairo Road", "driver_id": str(uuid4())}, headers=officer)
    assert r.status_code == 422


def test_staff_reports_are_official_and_skip_citizen_rules():
    officer, _ = _user("officer")
    net = _network(officer)
    r = client.post("/api/incidents/", json={"incident_type": "road_closed", "road_id": net["ab"]["road_id"]},
                    headers=officer)
    assert r.status_code == 201, r.text
    assert r.json()["verification"] == "official"
    assert client.post(f"/api/incidents/{r.json()['id']}/confirm", headers=officer).status_code == 409
