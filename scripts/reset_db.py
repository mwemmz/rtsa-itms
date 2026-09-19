"""DANGER: wipe the database that DATABASE_URL points at and rebuild it from scratch.

    python -m scripts.reset_db

Drops the whole ``public`` schema (every table and all data), then runs
``alembic upgrade head`` and the seed. You must type the database name to confirm.
Never run this against a database anyone else is using.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.database import engine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    name = engine.url.database
    if engine.dialect.name != "postgresql":
        raise SystemExit("This script only resets PostgreSQL databases.")
    print(f"About to DELETE EVERYTHING in database '{name}' on {engine.url.host}:{engine.url.port}")
    if input(f"Type the database name ({name}) to continue: ").strip() != name:
        raise SystemExit("Cancelled. Nothing was changed.")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    print("Schema dropped. Rebuilding...")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "scripts.seed"], check=True, cwd=ROOT)
    print("Done: fresh schema created and demo data seeded.")


if __name__ == "__main__":
    main()
