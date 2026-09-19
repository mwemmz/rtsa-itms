"""Non-destructive end-to-end smoke test against whatever DATABASE_URL points at.

Use it to prove a real PostgreSQL (or Neon) database works with the platform
modules - the pytest suite is SQLite-only because it drops and recreates tables,
which you must never do to a shared database.

    alembic upgrade head
    python -m scripts.smoke_test

It creates clearly named ``smoke-*`` records (and one deactivated admin), never
deletes or modifies anything else, and exits non-zero on the first failure.
"""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.enforcement import Challan, Violation, ViolationType  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.models.vehicle import Vehicle  # noqa: E402

PASSWORD = "smoketest123"


def main() -> int:
    from fastapi.testclient import TestClient

    from main import app

    print(f"Database: {engine.dialect.name} @ {engine.url.render_as_string(hide_password=True)}")
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + detail if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    db = SessionLocal()
    admin = User(email=f"smoke-admin-{tag}@example.com", hashed_password=hash_password(PASSWORD),
                 full_name="Smoke Admin", role=UserRole.ADMIN)
    citizen = User(email=f"smoke-citizen-{tag}@example.com", hashed_password=hash_password(PASSWORD),
                   full_name="Smoke Citizen", role=UserRole.CITIZEN)
    db.add_all([admin, citizen])
    db.commit()
    vehicle = Vehicle(registration_number=f"SMK {tag[:4].upper()}", owner_name="Smoke Citizen", owner_id_number="0",
                      make="Test", model="Car", year=2020, user_id=citizen.id)
    db.add(vehicle)
    db.flush()
    violation = Violation(vehicle_id=vehicle.id, violation_type=ViolationType.SPEEDING, location="smoke test")
    db.add(violation)
    db.flush()
    challan = Challan(reference=f"SMOKE-{tag}", violation_id=violation.id, vehicle_id=vehicle.id, penalty_amount=1234,
                      due_date=datetime.utcnow() + timedelta(days=7))
    db.add(challan)
    db.commit()
    challan_id, admin_email, citizen_email = str(challan.id), admin.email, citizen.email

    try:
        def login(email):
            r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
            return {"Authorization": f"Bearer {r.json().get('access_token')}"}, r.status_code

        print("Auth & sessions")
        a, code = login(admin_email)
        check("admin login", code == 200)
        c, code = login(citizen_email)
        check("citizen login", code == 200)
        check("session-backed /me", client.get("/api/auth/me", headers=a).json().get("role") == "admin")
        check("citizen blocked from admin API", client.get("/api/admin/users", headers=c).status_code == 403)

        print("Payments")
        pay = client.post("/api/payments/", json={"payment_type": "fine", "related_entity_id": challan_id}, headers=c)
        check("pay fine (server amount)", pay.status_code == 201 and pay.json().get("amount") == 1234, pay.text[:120])
        pid = pay.json().get("id")
        check("receipt PDF", client.get(f"/api/payments/{pid}/receipt.pdf", headers=c).content[:4] == b"%PDF")
        check("double payment refused", client.post("/api/payments/", json={"payment_type": "fine",
              "related_entity_id": challan_id}, headers=c).status_code == 409)
        check("partial refund", client.post(f"/api/payments/{pid}/refund", json={"amount": 34, "reason": "smoke"},
                                           headers=a).status_code == 200)
        check("revenue summary", client.get("/api/payments/summary", headers=a).status_code == 200)

        print("Reports & exports")
        d = client.get("/api/reports/dashboard", headers=a)
        check("analytics dashboard (SQL month bucketing)", d.status_code == 200, d.text[:160])
        for key in ("registrations", "licensing", "violations", "accidents", "psv", "revenue", "toll"):
            check(f"report {key}", client.get(f"/api/reports/{key}", headers=a).status_code == 200)
        for fmt, magic in (("csv", b"\xef\xbb\xbf"), ("xlsx", b"PK"), ("pdf", b"%PDF")):
            check(f"export {fmt}", client.get(f"/api/reports/revenue?format={fmt}", headers=a).content[:4].startswith(magic))

        print("Admin, settings, integration, ops")
        check("settings list", client.get("/api/admin/settings", headers=a).status_code == 200)
        agency = client.post("/api/integration/agencies", headers=a,
                             json={"name": f"smoke-agency-{tag}", "agency_type": "police"})
        check("register agency", agency.status_code == 201, agency.text[:120])
        key = agency.json().get("api_key", "")
        check("agency API call", client.get(f"/api/integration/police/vehicles/{vehicle.registration_number}",
                                            headers={"X-API-Key": key}).status_code == 200)
        check("integration monitoring", client.get("/api/integration/monitoring", headers=a).status_code == 200)
        check("readiness probe", client.get("/health/ready").status_code == 200)
        check("metrics", client.get("/api/system/metrics", headers=a).status_code == 200)
        bk = client.post("/api/system/backups", headers=a)
        check("backup + verify", bk.status_code == 200 and bk.json().get("verification", {}).get("ok") is True, bk.text[:160])
        check("audit trail written", len(client.get("/api/admin/audit-logs?limit=5", headers=a).json()) > 0)
    finally:
        cleanup = SessionLocal()
        for email in (admin_email, citizen_email):
            u = cleanup.query(User).filter(User.email == email).first()
            if u:
                u.is_active = False
        cleanup.commit()
        cleanup.close()
        db.close()

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
