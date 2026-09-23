"""CAPTCHA challenges for login and registration.

Two modes, chosen automatically by configuration:

* **Sandbox** (default - no ``CAPTCHA_SECRET_KEY`` set): a small, stateless
  arithmetic challenge. The question (and everything needed to check it) is
  packed into an opaque, HMAC-signed ``captcha_id`` handed to the client, so
  the server needs no session storage and no external service. This proves
  the client executed a request/response round trip and did basic arithmetic
  - it is **not** meaningful bot resistance, the same honest caveat this
  codebase gives the National ID sandbox adapter (see ``app.services.integration``).
* **Real provider** (``CAPTCHA_PROVIDER`` + ``CAPTCHA_SECRET_KEY`` set): reCAPTCHA
  v2/v3, hCaptcha or Cloudflare Turnstile. The frontend renders that provider's
  widget with ``CAPTCHA_SITE_KEY`` and submits the token it returns as
  ``captcha_token``; the server verifies it against the provider's siteverify
  endpoint.

Swapping in a real provider needs no code changes - only the three env vars.
"""

import base64
import hmac
import secrets
import time

import httpx

from app.core.config import settings

CHALLENGE_TTL_SECONDS = 300

PROVIDER_VERIFY_URLS = {
    "recaptcha": "https://www.google.com/recaptcha/api/siteverify",
    "hcaptcha": "https://hcaptcha.com/siteverify",
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
}


def using_real_provider() -> bool:
    provider = settings.CAPTCHA_PROVIDER.strip().lower()
    return provider in PROVIDER_VERIFY_URLS and bool(settings.CAPTCHA_SECRET_KEY)


def _sign(payload: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), payload.encode(), "sha256").hexdigest()


def _pack(a: int, b: int, expires: int) -> str:
    nonce = secrets.token_hex(6)
    payload = f"{a}.{b}.{expires}.{nonce}"
    raw = f"{payload}.{_sign(payload)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _unpack(challenge_id: str) -> tuple[int, int, int] | None:
    try:
        padded = challenge_id + "=" * (-len(challenge_id) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        a_s, b_s, expires_s, nonce, sig = raw.split(".")
    except (ValueError, UnicodeDecodeError):
        return None
    payload = f"{a_s}.{b_s}.{expires_s}.{nonce}"
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    return int(a_s), int(b_s), int(expires_s)


def new_challenge() -> dict:
    """Public info for GET /api/auth/captcha - what the client should render."""
    if using_real_provider():
        return {"provider": settings.CAPTCHA_PROVIDER.strip().lower(), "site_key": settings.CAPTCHA_SITE_KEY}
    a, b = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
    expires = int(time.time()) + CHALLENGE_TTL_SECONDS
    return {
        "provider": "sandbox",
        "captcha_id": _pack(a, b, expires),
        "question": f"What is {a} + {b}?",
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


def _verify_sandbox(fields: dict) -> bool:
    challenge_id = fields.get("captcha_id")
    answer = fields.get("captcha_answer")
    if not challenge_id or answer in (None, ""):
        return False
    unpacked = _unpack(str(challenge_id))
    if unpacked is None:
        return False
    a, b, expires = unpacked
    if expires < int(time.time()):
        return False
    try:
        return int(str(answer).strip()) == a + b
    except ValueError:
        return False


def _verify_provider(fields: dict) -> bool:
    token = fields.get("captcha_token")
    if not token:
        return False
    provider = settings.CAPTCHA_PROVIDER.strip().lower()
    try:
        resp = httpx.post(
            PROVIDER_VERIFY_URLS[provider],
            data={"secret": settings.CAPTCHA_SECRET_KEY, "response": token},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return False
    return bool(data.get("success"))


def verify(fields: dict) -> bool:
    """Verify a solved challenge. Never raises - bad/missing input just fails."""
    if using_real_provider():
        return _verify_provider(fields)
    return _verify_sandbox(fields)
