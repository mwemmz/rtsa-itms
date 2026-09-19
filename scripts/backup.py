"""Database backup and verification.

Usage:
    python -m scripts.backup                # write a backup now (+ prune old ones)
    python -m scripts.backup --verify FILE  # check a backup file is readable/complete

Two formats, chosen automatically:

* ``pg_dump`` custom format (``.dump``) when the ``pg_dump`` binary is on PATH
  and the database is PostgreSQL. This is the format to use for real disaster
  recovery (restore with ``pg_restore``).
* Portable JSON snapshot (``.json.gz``) otherwise. It needs nothing but Python,
  works on SQLite and PostgreSQL, and is restored with ``scripts/restore.py``.
"""

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.models  # noqa: E402,F401  (register every table on Base.metadata)
from app.core.config import settings  # noqa: E402
from app.core.database import Base, engine  # noqa: E402

FORMAT_VERSION = 1


def backup_dir() -> Path:
    path = Path(settings.BACKUP_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return {"__t": "dt", "v": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"__t": "uuid", "v": str(value)}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Decimal):
        return {"__t": "dec", "v": str(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"__t": "b", "v": bytes(value).hex()}
    raise TypeError(f"Cannot serialise {type(value)!r}")


def json_backup(target: Path) -> Path:
    tables: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = conn.execute(table.select()).mappings().all()
            tables[table.name] = [dict(r) for r in rows]
    payload = {
        "format": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dialect": engine.dialect.name,
        "counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
    }
    with gzip.open(target, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, default=_json_default)
    return target


def pg_dump_backup(target: Path) -> Path:
    url = urlparse(settings.DATABASE_URL)
    env = None
    if url.password:
        import os

        env = {**os.environ, "PGPASSWORD": url.password}
    cmd = ["pg_dump", "-Fc", "-h", url.hostname or "localhost", "-p", str(url.port or 5432),
           "-U", url.username or "postgres", "-f", str(target), url.path.lstrip("/")]
    subprocess.run(cmd, check=True, env=env, capture_output=True, timeout=900)
    return target


def prune(directory: Path, keep: int) -> list[Path]:
    files = sorted(
        [p for p in directory.iterdir() if p.name.startswith("rtsa-itms-") and p.suffix in (".dump", ".gz")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = files[keep:]
    for p in removed:
        p.unlink(missing_ok=True)
    return removed


def run_backup(prefer_pg_dump: bool = True) -> Path:
    directory = backup_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if prefer_pg_dump and engine.dialect.name == "postgresql" and shutil.which("pg_dump"):
        path = pg_dump_backup(directory / f"rtsa-itms-{stamp}.dump")
    else:
        path = json_backup(directory / f"rtsa-itms-{stamp}.json.gz")
    prune(directory, settings.BACKUP_RETENTION)
    return path


def verify_backup(path: Path) -> dict:
    """Confirm a backup file is complete and report what it contains."""
    if path.suffix == ".dump":
        if not shutil.which("pg_restore"):
            return {"ok": False, "error": "pg_restore not installed; cannot inspect .dump files"}
        proc = subprocess.run(["pg_restore", "--list", str(path)], capture_output=True, text=True)
        return {"ok": proc.returncode == 0, "format": "pg_dump", "entries": len(proc.stdout.splitlines())}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, EOFError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    counts = data.get("counts", {})
    actual = {name: len(rows) for name, rows in data.get("tables", {}).items()}
    return {"ok": counts == actual, "format": "json", "created_at": data.get("created_at"),
            "tables": len(actual), "rows": sum(actual.values())}


def list_backups() -> list[dict]:
    directory = backup_dir()
    out = []
    for p in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.name.startswith("rtsa-itms-"):
            out.append({"file": p.name, "size_bytes": p.stat().st_size,
                        "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="RTSA ITMS database backup")
    parser.add_argument("--verify", metavar="FILE", help="verify an existing backup instead of creating one")
    parser.add_argument("--json", action="store_true", help="force the portable JSON format")
    args = parser.parse_args()
    if args.verify:
        result = verify_backup(Path(args.verify))
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 1)
    path = run_backup(prefer_pg_dump=not args.json)
    print(f"Backup written: {path} ({path.stat().st_size:,} bytes)")
    print(json.dumps(verify_backup(path)))


if __name__ == "__main__":
    main()
