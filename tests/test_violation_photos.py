"""Photo evidence on a violation: attach, list, view, and the limits on what's stored."""

import hashlib
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TRUST_PROXY_HEADERS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402
import app.models  # noqa: F401, E402  # register all models on Base.metadata
import app.core.ratelimit as ratelimit  # noqa: E402
from app.core.database import Base, engine  # noqa: E402

Base.metadata.create_all(bind=engine)

from app.api.enforcement import MAX_PHOTO_BYTES  # noqa: E402
from main import app  # noqa: E402
from tests.conftest import create_user  # noqa: E402

client = TestClient(app)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + os.urandom(2000)
PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(500)
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + os.urandom(300)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    yield
    with ratelimit._lock:
        ratelimit._attempts.clear()


def _login(role: str) -> dict:
    email, _ = create_user(role)
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _violation(h: dict) -> str:
    v = client.post("/api/vehicles/", headers=h, json={
        "registration_number": f"PH {uuid4().hex[:4].upper()}", "owner_name": "Photo Owner",
        "owner_id_number": uuid4().hex[:8], "make": "Honda", "model": "Fit", "year": 2016,
    })
    assert v.status_code in (200, 201), v.text
    r = client.post("/api/enforcement/violations", headers=h, json={
        "vehicle_id": v.json()["id"], "violation_type": "illegal_parking", "location": "Cairo Road",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(h: dict, vid: str, data: bytes, name: str = "scene.jpg", declared: str = "image/jpeg"):
    return client.post(f"/api/enforcement/violations/{vid}/photos", headers=h, files={"file": (name, data, declared)})


def test_a_photo_is_stored_fingerprinted_and_served_back_unchanged():
    h = _login("officer")
    vid = _violation(h)
    r = _upload(h, vid, JPEG)
    assert r.status_code == 201, r.text
    photo = r.json()
    assert photo["content_type"] == "image/jpeg"
    assert photo["size_bytes"] == len(JPEG)
    assert photo["sha256"] == hashlib.sha256(JPEG).hexdigest()

    listed = client.get(f"/api/enforcement/violations/{vid}/photos", headers=h).json()
    assert [p["id"] for p in listed] == [photo["id"]]

    img = client.get(f"/api/enforcement/violations/{vid}/photos/{photo['id']}", headers=h)
    assert img.status_code == 200
    assert img.content == JPEG
    assert img.headers["content-type"] == "image/jpeg"
    assert img.headers["x-content-type-options"] == "nosniff"


def test_the_violation_list_counts_its_photos():
    h = _login("officer")
    vid = _violation(h)
    _upload(h, vid, JPEG)
    _upload(h, vid, PNG, "plate.png", "image/png")
    rows = client.get("/api/enforcement/violations?mine=true", headers=h).json()
    assert next(v for v in rows if v["id"] == vid)["photo_count"] == 2


def test_the_type_comes_from_the_bytes_not_the_name():
    h = _login("officer")
    vid = _violation(h)
    # A WebP sent as "photo.jpg" is stored and served as WebP...
    r = _upload(h, vid, WEBP, "photo.jpg", "image/jpeg")
    assert r.status_code == 201, r.text
    assert r.json()["content_type"] == "image/webp"
    # ...and a script dressed up as a JPEG is refused.
    fake = _upload(h, vid, b"<script>alert(1)</script>", "evil.jpg", "image/jpeg")
    assert fake.status_code == 415


def test_svg_is_refused():
    h = _login("officer")
    vid = _violation(h)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert _upload(h, vid, svg, "x.svg", "image/svg+xml").status_code == 415


def test_a_photo_over_the_size_limit_is_refused():
    h = _login("officer")
    vid = _violation(h)
    big = b"\xff\xd8\xff\xe0" + b"\x00" * MAX_PHOTO_BYTES
    assert _upload(h, vid, big).status_code == 413


def test_at_most_five_photos_per_violation():
    h = _login("officer")
    vid = _violation(h)
    for _ in range(5):
        assert _upload(h, vid, JPEG).status_code == 201
    assert _upload(h, vid, JPEG).status_code == 409


def test_citizens_cannot_add_or_view_photos():
    officer = _login("officer")
    vid = _violation(officer)
    photo_id = _upload(officer, vid, JPEG).json()["id"]
    citizen = _login("citizen")
    assert _upload(citizen, vid, JPEG).status_code == 403
    assert client.get(f"/api/enforcement/violations/{vid}/photos", headers=citizen).status_code == 403
    assert client.get(f"/api/enforcement/violations/{vid}/photos/{photo_id}", headers=citizen).status_code == 403


def test_a_photo_is_only_served_under_its_own_violation():
    h = _login("officer")
    vid = _violation(h)
    other = _violation(h)
    photo_id = _upload(h, vid, JPEG).json()["id"]
    assert client.get(f"/api/enforcement/violations/{other}/photos/{photo_id}", headers=h).status_code == 404


def test_unknown_violation_is_404():
    h = _login("officer")
    assert _upload(h, str(uuid4()), JPEG).status_code == 404


def test_the_ticket_mentions_the_photos():
    h = _login("officer")
    vid = _violation(h)
    _upload(h, vid, JPEG)
    from app.api.enforcement import _ticket
    from app.core.database import SessionLocal
    from app.models.enforcement import Challan

    db = SessionLocal()
    try:
        challan = db.query(Challan).filter(Challan.violation_id == vid).one()
        _, _, lines = _ticket(db, str(challan.id))
        assert ("Photos", "1 on file with RTSA") in lines
    finally:
        db.close()
