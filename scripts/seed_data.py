"""
Full seed script — run once against a fresh DB to populate demo data.

Usage:
    python scripts/seed_data.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.citizen import User, UserRole
from app.services.notifications import seed_system_templates
from app.scripts.seed_defaults import seed_defaults


def run():
    db = SessionLocal()
    try:
        print("Seeding notification templates...")
        seed_system_templates(db)

        print("Seeding system settings and thresholds...")
        seed_defaults(db)

        print("Seeding demo users...")
        demo_users = [
            {"email": "admin@rtsa.gov.zm",    "full_name": "System Administrator", "role": UserRole.ADMIN,     "password": "Admin1234!"},
            {"email": "officer@rtsa.gov.zm",  "full_name": "Traffic Officer Demo", "role": UserRole.OFFICER,   "password": "Officer123!"},
            {"email": "inspector@rtsa.gov.zm","full_name": "Vehicle Inspector Demo","role": UserRole.INSPECTOR, "password": "Inspect123!"},
            {"email": "auditor@rtsa.gov.zm",  "full_name": "Audit Officer Demo",   "role": UserRole.AUDITOR,   "password": "Audit1234!"},
            {"email": "citizen@example.com",  "full_name": "John Banda",           "role": UserRole.CITIZEN,   "password": "Citizen123!", "nrc_number": "123456/10/1"},
        ]
        for u in demo_users:
            existing = db.query(User).filter(User.email == u["email"]).first()
            if not existing:
                user = User(
                    email=u["email"],
                    full_name=u["full_name"],
                    role=u["role"],
                    hashed_password=hash_password(u["password"]),
                    nrc_number=u.get("nrc_number"),
                    is_active=True,
                    is_verified=True,
                )
                db.add(user)
                print(f"  Created: {u['email']} ({u['role'].value})")
            else:
                print(f"  Exists:  {u['email']}")

        db.commit()
        print("\nSeed complete.")
    except Exception as exc:
        db.rollback()
        print(f"Seed failed: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
