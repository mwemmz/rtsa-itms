"""RFC 6238 TOTP (Google Authenticator compatible), dependency free."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def totp_now(secret: str, at: float | None = None, step: int = 30) -> str:
    return _hotp(secret, int((at if at is not None else time.time()) // step))


def verify(secret: str, code: str, window: int = 1, step: int = 30) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    counter = int(time.time() // step)
    return any(
        hmac.compare_digest(_hotp(secret, counter + d), code) for d in range(-window, window + 1)
    )


def provisioning_uri(secret: str, account: str, issuer: str = "RTSA ITMS") -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"
    )
