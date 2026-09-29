"""Load realistic volumes into a scratch database and time the heavy endpoints.

    DATABASE_URL=postgresql://user:pass@localhost:3330/rtsa_bench python -m scripts.benchmark

Refuses to run unless the target database's name contains "bench" - it writes
hundreds of thousands of rows. Create the scratch database first (and drop it
after). The schema is built with the real Alembic migrations, so the production
indexes are in place. Seeding is skipped if the data is already there, so the
timings can be re-run quickly; ``--reseed`` wipes and reloads.

Requests go through the app in-process (FastAPI TestClient), so the numbers are
app + database time without network. Prints a Markdown table.
"""

import argparse
import os
import random
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SEED = 42
PASSWORD = "bench-pass-123"


def _check_target() -> str:
    url = os.environ.get("DATABASE_URL", "")
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "bench" not in name.lower():
        raise SystemExit(
            f"Refusing to run against database {name!r}: set DATABASE_URL to a scratch database "
            "whose name contains 'bench' (this loads hundreds of thousands of rows)."
        )
    return name


def _chunks(rows, size=5000):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def seed(engine, toll_rows: int, vehicles: int, violations: int, payments: int) -> dict:
    from sqlalchemy import insert

    from app.core.security import hash_password
    from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
    from app.models.payment import Payment, PaymentStatus, PaymentType
    from app.models.toll import TollComplianceResult, TollTransaction
    from app.models.user import User, UserRole
    from app.models.vehicle import Vehicle, VehicleStatus

    rng = random.Random(SEED)
    now = datetime.now(timezone.utc)

    def when():  # spread across the reports' default 90-day window
        return now - timedelta(seconds=rng.randint(0, 89 * 86400))

    admin_id = uuid.uuid4()
    citizen_ids = [uuid.uuid4() for _ in range(2000)]
    users = [dict(id=admin_id, email="bench-admin@rtsa.test", hashed_password=hash_password(PASSWORD),
                  full_name="Bench Admin", role=UserRole.ADMIN, is_active=True)]
    pw = hash_password(PASSWORD)  # one hash for all citizens: bcrypt is slow on purpose
    users += [dict(id=i, email=f"bench-citizen-{n}@rtsa.test", hashed_password=pw, full_name=f"Citizen {n}",
                   role=UserRole.CITIZEN, is_active=True) for n, i in enumerate(citizen_ids)]

    vehicle_rows, plates = [], []
    for n in range(vehicles):
        plate = f"B{n:06d}"
        plates.append(plate)
        vehicle_rows.append(dict(
            id=uuid.uuid4(), user_id=rng.choice(citizen_ids), registration_number=plate,
            owner_name=f"Owner {n}", owner_id_number=f"{n:06d}/10/1", make=rng.choice(["Toyota", "Nissan", "Mazda"]),
            model="Model", year=rng.randint(2005, 2025), status=VehicleStatus.ACTIVE,
            is_blacklisted=rng.random() < 0.01, registration_date=when(),
        ))
    vehicle_ids = [v["id"] for v in vehicle_rows]

    violation_rows, challan_rows = [], []
    for n in range(violations):
        vid, vio_id, ts = rng.choice(vehicle_ids), uuid.uuid4(), when()
        violation_rows.append(dict(id=vio_id, vehicle_id=vid, violation_type=rng.choice(list(ViolationType)),
                                   location="Great East Road", timestamp=ts, created_at=ts))
        challan_rows.append(dict(id=uuid.uuid4(), reference=f"CH-B{n:07d}", violation_id=vio_id, vehicle_id=vid,
                                 penalty_amount=rng.choice([30000, 60000, 150000]), due_date=ts + timedelta(days=30),
                                 status=rng.choice([ChallanStatus.UNPAID, ChallanStatus.PAID, ChallanStatus.OVERDUE]),
                                 created_at=ts))

    payment_rows = []
    for n in range(payments):
        ts = when()
        payment_rows.append(dict(
            id=uuid.uuid4(), reference=f"PAY-B{n:07d}", payment_type=rng.choice(list(PaymentType)),
            amount=rng.choice([2000, 30000, 60000]), currency="ZMW",
            status=rng.choices(list(PaymentStatus), weights=[5, 85, 7, 3])[0], gateway="sandbox",
            paid_by=rng.choice(citizen_ids), paid_at=ts, created_at=ts, refunded_amount=0,
        ))

    counts = {"users": len(users), "vehicles": len(vehicle_rows), "violations": len(violation_rows),
              "challans": len(challan_rows), "payments": len(payment_rows), "toll_transactions": toll_rows}
    t0 = time.perf_counter()
    with engine.begin() as conn:
        for model, rows in [(User, users), (Vehicle, vehicle_rows), (Violation, violation_rows),
                            (Challan, challan_rows), (Payment, payment_rows)]:
            for chunk in _chunks(rows):
                conn.execute(insert(model), chunk)
    # toll transactions are generated per chunk rather than held in memory all at once
    done = 0
    while done < toll_rows:
        n = min(10_000, toll_rows - done)
        chunk = []
        for _ in range(n):
            i = rng.randrange(vehicles)
            ts = when()
            flagged = rng.random() < 0.08
            chunk.append(dict(id=uuid.uuid4(), vehicle_id=vehicle_ids[i], plate_number=plates[i],
                              gate_id=f"Gate-{rng.randint(1, 12):02d}", timestamp=ts,
                              compliance_result=TollComplianceResult.FLAGGED if flagged else TollComplianceResult.COMPLIANT,
                              flagged_issues="No insurance" if flagged else None, toll_amount=2000,
                              is_paid=not flagged, created_at=ts))
        with engine.begin() as conn:
            conn.execute(insert(TollTransaction), chunk)
        done += n
        print(f"  toll transactions: {done:,}/{toll_rows:,}", end="\r", flush=True)
    print()
    with engine.begin() as conn:
        conn.exec_driver_sql("ANALYZE") if engine.dialect.name == "postgresql" else None
    counts["seed_seconds"] = round(time.perf_counter() - t0, 1)
    return counts


def _time(fn, repeat=1):
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return samples


def run_benchmarks(toll_samples: int) -> list[tuple[str, str]]:
    from fastapi.testclient import TestClient

    from app.services import reports
    from main import app

    results: list[tuple[str, str]] = []

    def ok(resp):
        assert resp.status_code in (200, 201), f"{resp.request.url} -> {resp.status_code}: {resp.text[:200]}"
        return resp

    def ms(samples):
        return f"{statistics.median(samples):,.0f} ms" if len(samples) > 1 else f"{samples[0]:,.0f} ms"

    with TestClient(app) as client:
        token = ok(client.post("/api/auth/login", json={"email": "bench-admin@rtsa.test", "password": PASSWORD})).json()
        h = {"Authorization": f"Bearer {token['access_token']}"}

        reports.clear_cache()
        results.append(("Analytics dashboard, cold", ms(_time(lambda: ok(client.get("/api/reports/dashboard", headers=h))))))
        results.append(("Analytics dashboard, cached", ms(_time(lambda: ok(client.get("/api/reports/dashboard", headers=h)), 5))))
        for key in reports.REPORTS:
            results.append((f"Report `{key}` (JSON, 500 rows)",
                            ms(_time(lambda k=key: ok(client.get(f"/api/reports/{k}", headers=h)), 3))))
        for fmt, limit, label in [("csv", 50_000, "CSV, 50 000 rows"), ("xlsx", 50_000, "Excel, 50 000 rows"),
                                  ("pdf", 2_000, "PDF, 2 000 rows")]:
            results.append((f"Toll report export ({label})",
                            ms(_time(lambda f=fmt, n=limit: ok(client.get(f"/api/reports/toll?format={f}&limit={n}", headers=h))))))
        results.append(("Challans list (100)", ms(_time(lambda: ok(client.get("/api/enforcement/challans?limit=100", headers=h)), 5))))

        # the toll-gate decision is the latency-critical path (target: toll.compliance_target_ms)
        rng = random.Random(SEED)
        lat = []
        for _ in range(toll_samples):
            plate = f"B{rng.randrange(1000):06d}"
            lat += _time(lambda p=plate: ok(client.post("/api/toll/events", headers=h,
                                                         json={"plate_number": p, "gate_id": "Gate-01", "toll_amount": 2000})))
        lat.sort()
        p95 = lat[int(len(lat) * 0.95) - 1]
        results.append((f"Toll-gate decision, {toll_samples} requests",
                        f"p50 {statistics.median(lat):,.0f} ms, p95 {p95:,.0f} ms, max {lat[-1]:,.0f} ms"))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="RTSA ITMS performance benchmark (scratch database only)")
    parser.add_argument("--toll-rows", type=int, default=600_000)
    parser.add_argument("--vehicles", type=int, default=20_000)
    parser.add_argument("--violations", type=int, default=50_000)
    parser.add_argument("--payments", type=int, default=30_000)
    parser.add_argument("--toll-samples", type=int, default=200, help="toll-gate decisions to time")
    parser.add_argument("--reseed", action="store_true", help="wipe the scratch database and load fresh data")
    args = parser.parse_args()

    name = _check_target()
    os.environ.setdefault("RUN_MIGRATIONS_ON_STARTUP", "false")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import func, select, text

    from app.core.database import Base, engine
    from app.models.toll import TollTransaction

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    if args.reseed:
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        existing = conn.execute(select(func.count()).select_from(TollTransaction)).scalar()
        server = conn.exec_driver_sql("SELECT version()").scalar() if engine.dialect.name == "postgresql" else "SQLite"
    if existing:
        print(f"Database {name!r} already holds {existing:,} toll transactions - reusing it (--reseed to reload).")
        counts = {"toll_transactions": existing}
    else:
        print(f"Seeding {name!r}...")
        counts = seed(engine, args.toll_rows, args.vehicles, args.violations, args.payments)

    print("Timing...")
    results = run_benchmarks(args.toll_samples)

    print(f"\n**Server:** {server.split(',')[0]}  ")
    print("**Data:** " + ", ".join(f"{k.replace('_', ' ')} {v:,}" for k, v in counts.items()) + "\n")
    print("| Operation | Time |\n|---|---|")
    for label, value in results:
        print(f"| {label} | {value} |")


if __name__ == "__main__":
    main()
