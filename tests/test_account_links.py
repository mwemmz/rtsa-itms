"""Forgotten-password reset and email confirmation."""

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as ratelimit
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models.user import User
from app.services import account_links
from app.services import settings as runtime_settings
from main import app
from tests.conftest import create_user, random_nrc

client = TestClient(app)
PW = "password123"


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Capture outgoing email instead of sending it; background tasks run before TestClient returns."""
    sent: list[dict] = []
    monkeypatch.setattr(account_links.delivery, "send_email",
                        lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}))
    yield sent
    with ratelimit._lock:
        ratelimit._attempts.clear()
    runtime_settings.invalidate_cache()


def _token(mail: dict, kind: str) -> str:
    match = re.search(rf"#/{kind}\?token=(\S+)", mail["body"])
    assert match, mail["body"]
    return match.group(1)


def _login(email, password=PW):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _auth(email, password=PW):
    r = _login(email, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user(email) -> User:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


def _register(email):
    r = client.post("/api/auth/register", json={"email": email, "password": PW, "full_name": "Jane Banda",
                                                  "nrc_number": random_nrc()},
                    headers={"X-Forwarded-For": f"10.20.{uuid4().int % 250}.1"})
    assert r.status_code == 201, r.text
    return r


# ============================ email confirmation ============================

def test_existing_and_admin_created_accounts_count_as_confirmed():
    email, _ = create_user("citizen")
    assert _user(email).email_verified_at is not None
    assert client.get("/api/auth/me", headers=_auth(email)).json()["email_verified"] is True


def test_sign_up_sends_a_confirmation_link_that_confirms_the_address(outbox):
    email = f"new{uuid4().hex[:6]}@example.com"
    _register(email)
    assert _user(email).email_verified_at is None
    assert [m["to"] for m in outbox] == [email] and "Confirm your email" in outbox[0]["subject"]
    # links carry the token after "#", so it never reaches server logs
    assert "/#/verify?token=" in outbox[0]["body"]

    h = _auth(email)
    assert client.get("/api/auth/me", headers=h).json()["email_verified"] is False
    token = _token(outbox[0], "verify")
    r = client.post("/api/auth/verify-email", json={"token": token})
    assert r.status_code == 200 and r.json()["email"] == email
    assert client.get("/api/auth/me", headers=h).json()["email_verified"] is True
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200  # safe to repeat
    assert client.post("/api/auth/verify-email", json={"token": token + "x"}).status_code == 400


def test_unconfirmed_addresses_get_no_email_notifications():
    from app.models.notification import NotificationRule
    from app.services.notifications import notify

    email = f"unv{uuid4().hex[:6]}@example.com"
    _register(email)  # before opening a write transaction: SQLite allows one writer at a time
    db = SessionLocal()
    try:
        event = f"test_verify_{uuid4().hex[:6]}"
        db.add(NotificationRule(trigger_event=event, channels="in_app,email", title_template="t", body_template="b"))
        db.flush()  # sessions don't autoflush
        user = db.query(User).filter(User.email == email).first()
        channels = sorted(n.channel.value for n in notify(db, user.id, event, {}))
        assert channels == ["in_app"]  # the email may not be theirs yet
        user.email_verified_at = user.created_at
        db.flush()
        channels = sorted(n.channel.value for n in notify(db, user.id, event, {}, dedupe_key="again"))
        assert channels == ["email", "in_app"]
    finally:
        db.rollback()
        db.close()


def test_resend_confirmation_answers_the_same_for_anyone(outbox):
    email = f"rs{uuid4().hex[:6]}@example.com"
    _register(email)
    outbox.clear()
    known = client.post("/api/auth/verify-email/resend", json={"email": email.upper()})
    unknown = client.post("/api/auth/verify-email/resend", json={"email": f"nobody{uuid4().hex[:6]}@example.com"})
    confirmed, _ = create_user("citizen")
    already = client.post("/api/auth/verify-email/resend", json={"email": confirmed})
    assert known.status_code == unknown.status_code == already.status_code == 202
    assert known.json() == unknown.json() == already.json()
    assert [m["to"] for m in outbox] == [email]  # only the account actually waiting gets mail


def test_confirmation_can_be_required_before_sign_in(outbox):
    email = f"req{uuid4().hex[:6]}@example.com"
    _register(email)
    admin = _auth(create_user("admin")[0])
    assert client.put("/api/admin/settings/security.require_email_verification", json={"value": True},
                      headers=admin).status_code == 200
    try:
        r = _login(email)
        assert r.status_code == 403 and "Confirm your email" in r.json()["detail"]
        assert _login(email, "wrong-pass1").status_code == 401  # wrong password: can't probe whether it's confirmed
        client.post("/api/auth/verify-email", json={"token": _token(outbox[0], "verify")})
        assert _login(email).status_code == 200
    finally:
        client.delete("/api/admin/settings/security.require_email_verification", headers=admin)


# ============================ forgotten password ============================

def test_reset_request_gives_nothing_away(outbox):
    email, _ = create_user("citizen")
    real = client.post("/api/auth/password-reset/request", json={"email": email})
    fake = client.post("/api/auth/password-reset/request", json={"email": f"ghost{uuid4().hex[:6]}@example.com"})
    assert real.status_code == fake.status_code == 202 and real.json() == fake.json()
    assert [m["to"] for m in outbox] == [email] and "/#/reset?token=" in outbox[0]["body"]


def test_reset_sets_the_new_password_signs_out_everywhere_and_works_once(outbox):
    email, _ = create_user("citizen")
    old_session = _auth(email)
    client.post("/api/auth/password-reset/request", json={"email": email})
    token = _token(outbox[0], "reset")

    weak = client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "short"})
    assert weak.status_code == 400  # rejected, and the link still works afterwards
    ok = client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "brandnew123"})
    assert ok.status_code == 204

    assert client.get("/api/auth/me", headers=old_session).status_code == 401  # signed out everywhere
    assert _login(email, PW).status_code == 401
    assert _login(email, "brandnew123").status_code == 200
    again = client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "another123"})
    assert again.status_code == 400  # a link works once
    assert any("password was changed" in m["subject"] for m in outbox)  # heads-up in case it wasn't them


def test_using_the_newest_link_kills_older_ones(outbox):
    email, _ = create_user("citizen")
    client.post("/api/auth/password-reset/request", json={"email": email})
    client.post("/api/auth/password-reset/request", json={"email": email})
    first, second = _token(outbox[0], "reset"), _token(outbox[1], "reset")
    assert client.post("/api/auth/password-reset/confirm", json={"token": second, "new_password": "brandnew123"}).status_code == 204
    assert client.post("/api/auth/password-reset/confirm", json={"token": first, "new_password": "other1234"}).status_code == 400


def test_reset_clears_a_lockout(outbox):
    email, _ = create_user("citizen")
    for i in range(5):
        client.post("/api/auth/login", json={"email": email, "password": "wrong-pass1"},
                    headers={"X-Forwarded-For": f"10.30.0.{i}"})
    assert _login(email).status_code == 423
    client.post("/api/auth/password-reset/request", json={"email": email})
    client.post("/api/auth/password-reset/confirm",
                json={"token": _token(outbox[-1], "reset"), "new_password": "brandnew123"})
    assert _login(email, "brandnew123").status_code == 200


def test_links_cannot_be_forged_mixed_up_or_used_late(outbox):
    email, user_id = create_user("citizen")
    access = _auth(email)["Authorization"].split()[1]
    # an access token isn't a reset link, and a reset link isn't an access token
    assert client.post("/api/auth/password-reset/confirm", json={"token": access, "new_password": "brandnew123"}).status_code == 400
    client.post("/api/auth/password-reset/request", json={"email": email})
    reset = _token(outbox[0], "reset")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {reset}"}).status_code == 401
    assert client.post("/api/auth/verify-email", json={"token": reset}).status_code == 400
    # expired
    user = _user(email)
    stale = create_access_token({"sub": str(user_id), "typ": "password_reset",
                                 "pwh": account_links._fingerprint(user.hashed_password)},
                                expires_delta=timedelta(minutes=-1))
    assert client.post("/api/auth/password-reset/confirm", json={"token": stale, "new_password": "brandnew123"}).status_code == 400


def test_reset_requests_are_throttled_per_address(outbox):
    email, _ = create_user("citizen")
    codes = [client.post("/api/auth/password-reset/request", json={"email": email},
                         headers={"X-Forwarded-For": f"10.40.0.{i}"}).status_code for i in range(4)]
    assert codes == [202, 202, 202, 429]  # even from different networks, nobody can flood one inbox
    assert len(outbox) == 3


def test_the_real_owner_can_take_back_an_address_someone_else_registered(outbox):
    email = f"owner{uuid4().hex[:6]}@example.com"
    _register(email)  # a squatter signs up with the owner's address
    squatter = _auth(email)
    outbox.clear()

    taken = client.post("/api/auth/register", json={"email": email, "password": PW, "full_name": "Real Owner",
                                                    "nrc_number": random_nrc()})
    assert taken.status_code == 400 and "Forgot password" in taken.json()["detail"]
    client.post("/api/auth/password-reset/request", json={"email": email})  # the link lands in the owner's inbox
    client.post("/api/auth/password-reset/confirm",
                json={"token": _token(outbox[0], "reset"), "new_password": "ownersown123"})

    assert client.get("/api/auth/me", headers=squatter).status_code == 401  # squatter is out
    owner = _auth(email, "ownersown123")
    assert client.get("/api/auth/me", headers=owner).json()["email_verified"] is True
