"""Get a local copy running: check the environment, prepare the database, seed it.

    python -m scripts.setup_local            # check + migrate + seed (asks before creating a database)
    python -m scripts.setup_local --yes      # also create the database without asking
    python -m scripts.setup_local --check    # only report problems, change nothing

Safe to run any number of times - after pulling new code, run it again. It never
deletes data: if the database belongs to different code it stops and tells you
what to do.
"""

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "  [ok]  ", "  [!!]  ", "  [XX]  "
# import name for each requirement whose package name differs
IMPORT_NAMES = {"uvicorn[standard]": "uvicorn", "psycopg2-binary": "psycopg2", "pydantic-settings": "pydantic_settings",
                "python-jose[cryptography]": "jose", "python-multipart": "multipart", "pyyaml": "yaml"}


def fail(msg: str, fix: str) -> None:
    print(f"{FAIL}{msg}\n        -> {fix}")
    sys.exit(1)


def check_python() -> None:
    want = (ROOT / "runtime.txt").read_text().strip() if (ROOT / "runtime.txt").exists() else "3.11"
    have = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info < (3, 11):
        fail(f"Python {have} is too old", f"install Python {want} (the version Render uses)")
    note = "" if want.startswith(have) else f" (Render uses {want}; newer works, older does not)"
    print(f"{OK}Python {have}{note}")


def check_packages() -> None:
    missing = []
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = line.split(">")[0].split("=")[0].split("<")[0].strip()
        module = IMPORT_NAMES.get(name, IMPORT_NAMES.get(name.lower(), name.split("[")[0].replace("-", "_").lower()))
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(name)
    if missing:
        fail(f"missing packages: {', '.join(missing)}", "pip install -r requirements.txt")
    print(f"{OK}all required packages installed")


def check_env() -> str:
    env, example = ROOT / ".env", ROOT / ".env.example"
    # Settings may come from real environment variables instead (Render, CI, a shell
    # export); a .env file is only needed when DATABASE_URL isn't set that way.
    if not env.exists() and not os.environ.get("DATABASE_URL"):
        env.write_text(example.read_text())
        fail(".env did not exist - created it from .env.example",
             "open .env, set DATABASE_URL to your database (and SECRET_KEY), then run this again")
    from app.core.config import settings

    url = settings.DATABASE_URL
    if any(p in url for p in ("user:password@", "<user>", "<password>", "ep-xxx")):
        fail("DATABASE_URL still has the example placeholders (check .env or your environment)",
             "put your real Postgres user and password in DATABASE_URL, e.g. "
             "postgresql://postgres:YOURPASSWORD@localhost:3330/rtsa_itms  (no < > brackets)")
    if settings.SECRET_KEY in ("", "change-me-in-production", "your-secret-key-change-in-production"):
        print(f"{WARN}SECRET_KEY is the example value - fine locally, never in production")
    return url


def check_database(url: str, create: bool, check_only: bool) -> None:
    import sqlalchemy as sa
    from sqlalchemy.engine import make_url

    if url.startswith("sqlite"):
        print(f"{OK}using SQLite ({url})")
        return
    target = make_url(url)
    server = target.set(database="postgres")
    try:
        with sa.create_engine(server, isolation_level="AUTOCOMMIT").connect() as conn:
            exists = conn.execute(sa.text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                  {"n": target.database}).scalar()
            if not exists:
                if check_only:
                    fail(f"database '{target.database}' does not exist", "run: python -m scripts.setup_local --yes")
                if not create and input(f"  Database '{target.database}' does not exist. Create it? [y/N] ").strip().lower() != "y":
                    fail(f"database '{target.database}' does not exist", "create it, or run with --yes")
                conn.execute(sa.text(f'CREATE DATABASE "{target.database}"'))
                print(f"{OK}created database '{target.database}'")
    except sa.exc.OperationalError as exc:
        text = str(exc.orig).lower()
        where = f"{target.host}:{target.port or 5432}"
        if "password authentication failed" in text:
            fail(f"Postgres rejected user '{target.username}'", "check the user and password in DATABASE_URL "
                 "(special characters in the password must be URL-encoded, e.g. @ -> %40)")
        if "refused" in text or "could not connect" in text or "timeout" in text:
            fail(f"no Postgres server answering at {where}", "start PostgreSQL, or fix the host/port in DATABASE_URL")
        fail(f"cannot connect to Postgres at {where}: {exc.orig}", "check DATABASE_URL")
    print(f"{OK}connected to '{target.database}' on {target.host}:{target.port or 5432}")


def check_schema() -> None:
    import sqlalchemy as sa
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.core.database import engine

    known = {r.revision for r in ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).walk_revisions()}
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    reset_hint = ("this database was built by different code. Either point DATABASE_URL at a new, empty "
                  "database, or wipe and rebuild this one with: python -m scripts.reset_db  (deletes its data)")
    if "alembic_version" in tables:
        with engine.connect() as conn:
            current = {r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))}
        unknown = current - known
        if unknown:
            fail(f"database is at migration {', '.join(sorted(unknown))}, which this code doesn't have",
                 "if you just switched branches, switch back or pull the branch that added it; otherwise " + reset_hint)
        print(f"{OK}database migrations recognised ({', '.join(sorted(current))})")
    elif tables:
        fail(f"database already has {len(tables)} tables but no migration history", reset_hint)
    else:
        print(f"{OK}database is empty - it will be built from scratch")


def run(*args: str) -> None:
    result = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-3000:], sep="\n")
        fail(f"'{' '.join(args)}' failed (output above)", "fix the error above and run this again")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="create the database if it doesn't exist, without asking")
    parser.add_argument("--check", action="store_true", help="only check; change nothing")
    args = parser.parse_args()

    print("Checking your setup...")
    check_python()
    check_packages()
    url = check_env()
    check_database(url, create=args.yes, check_only=args.check)
    check_schema()
    if args.check:
        print("\nChecks passed. Run without --check to migrate and seed.")
        return

    print("Applying migrations...")
    run("-m", "alembic", "upgrade", "heads")  # "heads": works even while two branches each add a migration
    print(f"{OK}database schema up to date")
    print("Loading demo data...")
    run(str(ROOT / "scripts" / "seed.py"))
    print(f"{OK}demo data present")
    print("""
Ready. Start the app with:

    python -m uvicorn main:app --reload

then open http://localhost:8000 and sign in as
    admin@rtsa.gov.zm / admin123   officer@rtsa.gov.zm / officer123   citizen@example.com / citizen123
""")


if __name__ == "__main__":
    main()
