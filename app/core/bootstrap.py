"""One-time startup tasks run by the API process.

Render's free tier does not support pre-deploy hooks, so migrations and seed
data are applied when the web service boots (gated by
``RUN_MIGRATIONS_ON_STARTUP=true``). Both operations are idempotent, so a
redeploy or a free-tier wake-up is a safe no-op after the first run.
"""

import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.logging import get_logger

logger = get_logger("bootstrap")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def upgrade_database() -> None:
    """Apply all pending Alembic migrations."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def seed_database() -> None:
    """Load default notification rules and demo data (idempotent)."""
    seed_script = PROJECT_ROOT / "scripts" / "seed.py"
    subprocess.run([sys.executable, str(seed_script)], check=True)


def run_startup_tasks() -> None:
    logger.info("Running startup tasks: migrations then seed")
    upgrade_database()
    seed_database()
    logger.info("Startup tasks complete")