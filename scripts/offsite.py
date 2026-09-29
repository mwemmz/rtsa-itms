"""Off-site backup copies in S3-compatible object storage.

Render's disk is wiped on every deploy, so a backup that only exists there is
lost exactly when you'd need it. When ``BACKUP_S3_BUCKET`` is set, each backup
is copied to object storage (Amazon S3, Cloudflare R2, Backblaze B2, MinIO)
after it's written, checked, and old copies beyond ``BACKUP_S3_RETENTION`` are
removed.

Backups hold personal data (names, NRC numbers, payments), so with
``BACKUP_ENCRYPTION_KEY`` set they're encrypted (Fernet) before upload and stored
as ``<name>.enc``. That key is deliberately separate from ``ENCRYPTION_KEY`` /
``SECRET_KEY``: if the server and its secrets are lost, the backups must still be
readable - so keep a copy of it outside this deployment.

    python -m scripts.offsite --list
    python -m scripts.offsite --download latest     # into BACKUP_DIR, decrypted
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.s3 import S3Client, S3Error  # noqa: E402

ENCRYPTED_SUFFIX = ".enc"
_client_override: S3Client | None = None  # tests inject a client talking to a fake bucket


def configured() -> bool:
    return bool(settings.BACKUP_S3_BUCKET)


def encrypted() -> bool:
    return bool(settings.BACKUP_ENCRYPTION_KEY)


def _client() -> S3Client:
    if _client_override is not None:
        return _client_override
    missing = [name for name in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY")
               if not getattr(settings, name)]
    if missing:
        raise S3Error("Off-site backups are misconfigured: set " + ", ".join(missing))
    return S3Client(settings.BACKUP_S3_ENDPOINT, settings.BACKUP_S3_BUCKET, settings.BACKUP_S3_ACCESS_KEY_ID,
                    settings.BACKUP_S3_SECRET_ACCESS_KEY, settings.BACKUP_S3_REGION)


def _fernet() -> Fernet:
    try:
        return Fernet(settings.BACKUP_ENCRYPTION_KEY.encode())
    except ValueError as exc:
        raise S3Error("BACKUP_ENCRYPTION_KEY is not a valid Fernet key") from exc


def _key(name: str) -> str:
    return settings.BACKUP_S3_PREFIX + name


def upload(path: Path) -> dict:
    """Copy one backup off-site, confirm it arrived intact, and prune old copies."""
    data = path.read_bytes()
    name = path.name
    if encrypted():
        data = _fernet().encrypt(data)
        name += ENCRYPTED_SUFFIX
    client = _client()
    key = _key(name)
    client.put_object(key, data)
    stored = client.head_object(key)
    if stored != len(data):
        raise S3Error(f"upload of {key} could not be confirmed (stored {stored} of {len(data)} bytes)")
    removed = prune(settings.BACKUP_S3_RETENTION)
    return {"key": key, "bytes": len(data), "encrypted": encrypted(), "pruned": removed}


def list_remote() -> list[dict]:
    objects = [o for o in _client().list_objects(settings.BACKUP_S3_PREFIX)
               if Path(o.key).name.startswith("rtsa-itms-")]
    objects.sort(key=lambda o: Path(o.key).name, reverse=True)  # names embed a UTC timestamp
    return [{"key": o.key, "size_bytes": o.size, "last_modified": o.last_modified} for o in objects]


def prune(keep: int) -> list[str]:
    stale = [o["key"] for o in list_remote()[keep:]]
    client = _client()
    for key in stale:
        client.delete_object(key)
    return stale


def download(key: str, dest_dir: Path) -> Path:
    """Fetch a backup (``latest`` or a full key) into ``dest_dir``, decrypting if needed."""
    if key == "latest":
        items = list_remote()
        if not items:
            raise S3Error("No off-site backups found")
        key = items[0]["key"]
    data = _client().get_object(key)
    name = Path(key).name
    if name.endswith(ENCRYPTED_SUFFIX):
        if not encrypted():
            raise S3Error(f"{name} is encrypted: set BACKUP_ENCRYPTION_KEY to the key it was made with")
        try:
            data = _fernet().decrypt(data)
        except InvalidToken as exc:
            raise S3Error(f"{name} could not be decrypted with this BACKUP_ENCRYPTION_KEY") from exc
        name = name[: -len(ENCRYPTED_SUFFIX)]
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(data)
    return target


def status() -> dict:
    """What the System health page shows. Never raises."""
    info = {"configured": configured(), "encrypted": encrypted(), "bucket": settings.BACKUP_S3_BUCKET or None,
            "count": 0, "latest": None, "error": None}
    if not info["configured"]:
        return info
    try:
        items = list_remote()
        info.update(count=len(items), latest=items[0] if items else None)
    except Exception as exc:  # noqa: BLE001 - a storage outage must not break the admin page
        info["error"] = str(exc)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="RTSA ITMS off-site backups")
    parser.add_argument("--list", action="store_true", help="list off-site backups, newest first")
    parser.add_argument("--download", metavar="KEY", help="download a backup ('latest' or a full key) into BACKUP_DIR")
    args = parser.parse_args()
    if not configured():
        raise SystemExit("Off-site backups are not configured (set BACKUP_S3_BUCKET and friends).")
    if args.download:
        path = download(args.download, Path(settings.BACKUP_DIR))
        print(f"Downloaded {path}")
    else:
        for item in list_remote():
            print(f"{item['last_modified']}  {item['size_bytes']:>12,}  {item['key']}")


if __name__ == "__main__":
    main()
