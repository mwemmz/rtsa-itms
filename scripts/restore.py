"""Restore a database from a backup made by ``scripts.backup``.

Usage:
    python -m scripts.restore backups/rtsa-itms-20260101T020000Z.json.gz --yes
    python -m scripts.restore backups/rtsa-itms-20260101T020000Z.dump --yes

Safety: restoring **replaces every row** in the target database, so the script
refuses to run without ``--yes`` and refuses a non-empty target unless
``--wipe`` is also given. Point ``DATABASE_URL`` at the database to restore
into (for a rehearsal use a scratch database, never production).

JSON snapshots need the schema to exist first (``alembic upgrade head``);
``.dump`` files are restored with ``pg_restore --clean --if-exists``.
"""

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import Base, engine  # noqa: E402


def _decode(value):
    if isinstance(value, dict) and "__t" in value:
        kind, raw = value["__t"], value["v"]
        if kind == "dt":
            return datetime.fromisoformat(raw)
        if kind == "uuid":
            return uuid.UUID(raw)
        if kind == "dec":
            return Decimal(raw)
        if kind == "b":
            return bytes.fromhex(raw)
    return value


def restore_json(path: Path, wipe: bool) -> dict[str, int]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != 1:
        raise SystemExit(f"Unsupported backup format: {data.get('format')!r}")
    restored: dict[str, int] = {}
    with engine.begin() as conn:
        existing = sum(conn.execute(select(func.count()).select_from(t)).scalar() for t in Base.metadata.sorted_tables)
        if existing and not wipe:
            raise SystemExit("Target database is not empty. Re-run with --wipe to replace its contents.")
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(delete(table))
        for table in Base.metadata.sorted_tables:
            rows = data["tables"].get(table.name, [])
            if not rows:
                continue
            decoded = []
            for row in rows:
                item = {}
                for col in table.columns:
                    if col.name not in row:
                        continue
                    item[col.name] = _decode(row[col.name])
                decoded.append(item)
            conn.execute(table.insert(), decoded)
            restored[table.name] = len(decoded)
    return restored


def restore_pg(path: Path) -> None:
    if not shutil.which("pg_restore"):
        raise SystemExit("pg_restore is not installed")
    url = urlparse(settings.DATABASE_URL)
    import os

    env = {**os.environ, "PGPASSWORD": url.password} if url.password else None
    cmd = ["pg_restore", "--clean", "--if-exists", "--no-owner", "-h", url.hostname or "localhost",
           "-p", str(url.port or 5432), "-U", url.username or "postgres", "-d", url.path.lstrip("/"), str(path)]
    subprocess.run(cmd, check=True, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description="RTSA ITMS database restore")
    parser.add_argument("file")
    parser.add_argument("--yes", action="store_true", help="confirm you want to overwrite the target database")
    parser.add_argument("--wipe", action="store_true", help="allow replacing a non-empty database")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to run without --yes (this replaces the target database contents).")
    path = Path(args.file)
    print(f"Restoring {path.name} into {urlparse(settings.DATABASE_URL).hostname or 'sqlite'} ...")
    if path.suffix == ".dump":
        restore_pg(path)
        print("pg_restore complete.")
    else:
        counts = restore_json(path, wipe=args.wipe)
        print(f"Restored {sum(counts.values()):,} rows across {len(counts)} tables.")


if __name__ == "__main__":
    main()
