"""Off-site backups: S3 request signing, upload/verify/prune, encryption, restore download."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit
from xml.sax.saxutils import escape

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.s3 import EMPTY_SHA256, S3Client, S3Error, sign_v4
from main import app
from tests.conftest import create_user

client = TestClient(app)


# ---------------- request signing, against AWS's published examples ----------------

def _sig(headers):
    return headers["Authorization"].split("Signature=")[1]


def test_sigv4_matches_aws_test_suite():
    # aws-sig-v4-test-suite "get-vanilla"
    h = sign_v4("GET", "https://example.amazonaws.com/", {}, EMPTY_SHA256, "AKIDEXAMPLE",
                "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1", "service",
                datetime(2015, 8, 30, 12, 36, tzinfo=timezone.utc))
    assert _sig(h) == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def test_sigv4_matches_aws_s3_examples():
    key, secret, when = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", \
        datetime(2013, 5, 24, tzinfo=timezone.utc)
    get_object = sign_v4("GET", "https://examplebucket.s3.amazonaws.com/test.txt",
                         {"range": "bytes=0-9", "x-amz-content-sha256": EMPTY_SHA256}, EMPTY_SHA256,
                         key, secret, "us-east-1", "s3", when)
    assert _sig(get_object) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    list_objects = sign_v4("GET", "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J",
                           {"x-amz-content-sha256": EMPTY_SHA256}, EMPTY_SHA256, key, secret, "us-east-1", "s3", when)
    assert _sig(list_objects) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


# ---------------- a fake S3-compatible bucket ----------------

class FakeBucket:
    def __init__(self, bucket="rtsa-backups"):
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKTEST/")
        assert "/s3/aws4_request" in request.headers["authorization"]
        if self.fail:
            return httpx.Response(503, content=b"<Error><Code>SlowDown</Code></Error>")
        parts = urlsplit(str(request.url))
        path = unquote(parts.path).lstrip("/")
        bucket, _, key = path.partition("/")
        assert bucket == self.bucket
        if not key:  # ListObjectsV2
            prefix = parse_qs(parts.query).get("prefix", [""])[0]
            items = "".join(
                f"<Contents><Key>{escape(k)}</Key><Size>{len(v)}</Size>"
                f"<LastModified>2026-09-27T00:00:00.000Z</LastModified></Contents>"
                for k, v in sorted(self.objects.items()) if k.startswith(prefix)
            )
            body = (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"<IsTruncated>false</IsTruncated>{items}</ListBucketResult>")
            return httpx.Response(200, content=body.encode())
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if key not in self.objects:
            return httpx.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>")
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(self.objects[key]))})
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[key])
        if request.method == "DELETE":
            del self.objects[key]
            return httpx.Response(204)
        return httpx.Response(405)


@pytest.fixture()
def bucket(tmp_path, monkeypatch):
    from scripts import offsite

    fake = FakeBucket()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "BACKUP_S3_BUCKET", fake.bucket)
    monkeypatch.setattr(settings, "BACKUP_S3_PREFIX", "rtsa-itms/")
    monkeypatch.setattr(settings, "BACKUP_S3_RETENTION", 2)
    monkeypatch.setattr(settings, "BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(offsite, "_client_override",
                        S3Client("https://s3.test", fake.bucket, "AKTEST", "secret", transport=httpx.MockTransport(fake)))
    return fake


def _backup():
    from scripts.backup import backup_and_ship

    return backup_and_ship(prefer_pg_dump=False)


def test_backup_is_encrypted_uploaded_and_verified(bucket):
    result = _backup()
    assert result["verification"]["ok"] and result["offsite"]["ok"] and result["offsite"]["encrypted"]
    key = result["offsite"]["key"]
    assert key == f"rtsa-itms/{result['path'].name}.enc" and key in bucket.objects
    stored = bucket.objects[key]
    assert stored != result["path"].read_bytes()  # personal data never leaves in the clear
    assert Fernet(settings.BACKUP_ENCRYPTION_KEY.encode()).decrypt(stored) == result["path"].read_bytes()


def test_offsite_copies_are_pruned_to_retention(bucket):
    import time

    for _ in range(3):
        _backup()
        time.sleep(1.1)  # backup names carry a per-second UTC timestamp
    from scripts import offsite

    remaining = offsite.list_remote()
    assert len(remaining) == 2 and len(bucket.objects) == 2
    assert remaining[0]["key"] > remaining[1]["key"]  # newest first


def test_download_latest_decrypts_to_the_original(bucket, tmp_path):
    from scripts import offsite
    from scripts.backup import verify_backup

    original = _backup()["path"]
    restored = offsite.download("latest", tmp_path / "restore")
    assert restored.name == original.name and restored.read_bytes() == original.read_bytes()
    assert verify_backup(restored)["ok"]


def test_download_needs_the_right_key(bucket, tmp_path, monkeypatch):
    from scripts import offsite

    _backup()
    monkeypatch.setattr(settings, "BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(S3Error, match="could not be decrypted"):
        offsite.download("latest", tmp_path / "x")
    monkeypatch.setattr(settings, "BACKUP_ENCRYPTION_KEY", "")
    with pytest.raises(S3Error, match="is encrypted"):
        offsite.download("latest", tmp_path / "x")


def test_without_a_key_backups_are_stored_as_is(bucket, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_ENCRYPTION_KEY", "")
    result = _backup()
    assert result["offsite"]["ok"] and not result["offsite"]["encrypted"]
    assert bucket.objects[result["offsite"]["key"]] == result["path"].read_bytes()


def test_storage_outage_is_reported_not_raised(bucket):
    from scripts import offsite

    bucket.fail = True
    result = _backup()
    assert result["verification"]["ok"]  # the local backup still exists
    assert result["offsite"]["ok"] is False and "503" in result["offsite"]["error"]
    status = offsite.status()
    assert status["configured"] and "503" in status["error"]


def test_not_configured(monkeypatch, tmp_path):
    from scripts import offsite

    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "BACKUP_S3_BUCKET", "")
    assert _backup()["offsite"] is None
    assert offsite.status() == {"configured": False, "encrypted": bool(settings.BACKUP_ENCRYPTION_KEY),
                                "bucket": None, "count": 0, "latest": None, "error": None}


def test_admin_backup_endpoints_report_offsite(bucket):
    email, _ = create_user("admin")
    token = client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    made = client.post("/api/system/backups", headers=h)
    assert made.status_code == 200 and made.json()["offsite"]["ok"] and made.json()["offsite"]["encrypted"]
    listed = client.get("/api/system/backups", headers=h).json()["offsite"]
    assert listed["configured"] and listed["count"] == 1 and listed["latest"]["key"].endswith(".enc")
