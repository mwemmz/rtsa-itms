"""Test configuration — sets up an in-memory SQLite DB before any imports."""

import os

# Set env vars BEFORE importing anything from app so db.py picks up the URL
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_itms.db")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-pytest-only")
os.environ.setdefault("ENV", "development")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
import app.models  # noqa: F401 — register all tables


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(
        "sqlite:///./test_itms.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def db_session(db_engine):
    TestingSessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="session")
def client(db_engine):
    from app.main import app
    from app.core import db as db_module
    from sqlalchemy.orm import sessionmaker

    TestingSessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def override_get_db():
        s = TestingSessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[db_module.get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
