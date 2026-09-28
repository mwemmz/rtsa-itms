from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

_engine_kwargs: dict = {"pool_pre_ping": True}
if not settings.DATABASE_URL.startswith("sqlite"):
    # Connection pooling sized for concurrent load; recycle so managed
    # Postgres (Neon/PgBouncer) never hands us a stale connection.
    _engine_kwargs.update(
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=1800,
    )

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

if engine.dialect.name == "postgresql":
    # The app keeps every time in UTC. A Postgres server installed with a local
    # zone (e.g. Africa/Lusaka) would otherwise read naive UTC values as local
    # time: report windows drop the last hours and times stored in naive columns
    # shift. Neon and Render default to UTC; this makes every host behave alike.
    @event.listens_for(engine, "connect")
    def _session_in_utc(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("SET TIME ZONE 'UTC'")
        cursor.close()
        dbapi_connection.commit()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
