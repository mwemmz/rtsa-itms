import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TRUST_PROXY_HEADERS"] = "true"

import app.models  # noqa: F401, E402  # register all models on Base.metadata
from app.core.database import Base, engine  # noqa: E402


def pytest_configure(config):
    """Start every test session against a clean schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

def create_user(role: str = "citizen", password: str = "password123", **extra):
    """Insert a user directly (public registration only ever creates citizens)."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    db = SessionLocal()
    try:
        user = User(
            email=extra.pop("email", f"{role}_{uuid4().hex[:8]}@test.com"),
            hashed_password=hash_password(password),
            full_name=extra.pop("full_name", "Test User"),
            role=UserRole(role),
            # like existing accounts: confirmed, unless a test says otherwise
            email_verified_at=extra.pop("email_verified_at", datetime.now(timezone.utc)),
            **extra,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.email, user.id
    finally:
        db.close()


def random_nrc() -> str:
    """A well-formed NRC number for a test sign-up; one account per NRC, so make it unique."""
    from random import randint

    return f"{randint(100000, 999999)}/{randint(10, 99)}/{randint(1, 3)}"
