import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"

import app.models  # noqa: F401, E402  # register all models on Base.metadata
from app.core.database import Base, engine  # noqa: E402


def pytest_configure(config):
    """Start every test session against a clean schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)