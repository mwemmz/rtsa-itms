"""Fines past their due date become overdue (once, with the late surcharge); toll-gate fines match the offence."""

import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
from app.models.vehicle import Vehicle
from app.services.overdue import mark_overdue_challans
from main import app
from tests.conftest import create_user

client = TestClient(app)
PW = "password123"


def _login(role):
    email, _ = create_user(role)
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"email": email, "password": PW}).json()["access_token"]}


def _challan(due_in_days, amount=100000):
    db = SessionLocal()
    try:
        v = Vehicle(registration_number=f"OD{uuid.uuid4().hex[:6].upper()}", owner_name="O", owner_id_number="1",
                    make="Toyota", model="Hilux", year=2020)
        db.add(v)
        db.flush()
        viol = Violation(vehicle_id=v.id, violation_type=ViolationType.SPEEDING, location="Great East Rd")
        db.add(viol)
        db.flush()
        c = Challan(reference=f"OD-{uuid.uuid4().hex[:8].upper()}", violation_id=viol.id, vehicle_id=v.id,
                    penalty_amount=amount, due_date=datetime.utcnow() + timedelta(days=due_in_days))
        db.add(c)
        db.commit()
        return str(c.id)
    finally:
        db.close()


def test_past_due_fine_becomes_overdue_once_with_surcharge():
    late, fresh = _challan(-3), _challan(+5)
    db = SessionLocal()
    try:
        mark_overdue_challans(db)
        db.commit()
        mark_overdue_challans(db)  # a second run must not add the surcharge again
        db.commit()
        a, b = db.get(Challan, uuid.UUID(late)), db.get(Challan, uuid.UUID(fresh))
        assert a.status == ChallanStatus.OVERDUE and a.penalty_amount == 110000  # +10% by default
        assert b.status == ChallanStatus.UNPAID and b.penalty_amount == 100000
    finally:
        db.close()


def test_new_fine_uses_the_admin_due_days_setting():
    h = _login("officer")
    n = uuid.uuid4().hex[:6].upper()
    v = client.post("/api/vehicles/", headers=h, json={"registration_number": f"DU {n}", "owner_name": "O", "owner_id_number": "123456/10/1",
                                                      "make": "Toyota", "model": "Hilux", "year": 2020}).json()
    r = client.post("/api/enforcement/violations", headers=h, json={"vehicle_id": v["id"], "violation_type": "using_phone", "location": "Cairo Rd"})
    assert r.status_code == 201
    ch = [c for c in client.get("/api/enforcement/challans", headers=h).json() if c["violation_id"] == r.json()["id"]][0]
    due = datetime.fromisoformat(ch["due_date"].replace("Z", ""))
    assert 28 <= (due - datetime.utcnow()).days <= 30  # default setting is 30 days


def test_toll_gate_fine_is_the_offence_fine_not_a_flat_amount():
    h = _login("officer")
    n = uuid.uuid4().hex[:6].upper()
    plate = f"TG {n}"
    client.post("/api/vehicles/", headers=h, json={"registration_number": plate, "owner_name": "O", "owner_id_number": "123456/10/1",
                                                  "make": "Toyota", "model": "Hilux", "year": 2020})
    r = client.post("/api/toll/events", headers=h, json={"plate_number": plate, "gate_id": "G-1"})
    assert r.status_code in (200, 201) and r.json()["challan_created"]
    mine = [c for c in client.get("/api/enforcement/challans", headers=h).json() if c.get("plate_number") == plate or c.get("registration_number") == plate]
    assert mine, "the toll fine should be listed"
    assert mine[0]["penalty_amount"] == 1000000  # no insurance outranks no fitness certificate


def test_admin_fines_report_adds_up():
    email, _ = create_user("admin")
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"email": email, "password": PW}).json()["access_token"]}
    _challan(-3)  # one overdue fine, so there is something owed
    db = SessionLocal()
    try:
        mark_overdue_challans(db)
        db.commit()
    finally:
        db.close()
    f = client.get("/api/admin/reports/fines", headers=h)
    assert f.status_code == 200
    d = f.json()
    assert d["overdue"] > 0 and d["outstanding"] >= d["overdue"]
    assert sum(o["issued"] for o in d["by_offence"].values()) == d["issued"]
    assert sum(o["outstanding"] for o in d["by_offence"].values()) == d["outstanding"]
    s = client.get("/api/admin/reports/summary", headers=h).json()
    assert s["challans_overdue"] >= 1 and s["challans_unpaid"] >= s["challans_overdue"]
    citizen_h = _login("citizen")
    assert client.get("/api/admin/reports/fines", headers=citizen_h).status_code == 403


def test_demo_data_loads_only_when_asked(monkeypatch):
    from app.core import bootstrap
    calls = []
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    bootstrap.seed_demo_data()
    assert calls == []
    monkeypatch.setenv("SEED_DEMO_DATA", "true")
    bootstrap.seed_demo_data()
    assert len(calls) == 1 and calls[0][0][-1].endswith("seed_demo.py")
