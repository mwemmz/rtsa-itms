"""Database session factory — lazy engine creation so tests can set DATABASE_URL before import."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings

_engine = None
_SessionLocal = None


def _get_engine():
    global _engine
    if _engine is None:
        url = settings.database_url or "sqlite:///./itms_dev.db"
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    return _engine


def _get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


# Keep these as module-level names for backwards-compat
@property
def engine():
    return _get_engine()


def get_db() -> Session:
    SessionLocal = _get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Convenience alias for workers/scripts that need a session directly
def SessionLocal() -> Session:  # type: ignore[misc]
    return _get_session_factory()()
