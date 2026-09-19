"""Auth / security schemas."""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Login / Token
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# MFA
# ---------------------------------------------------------------------------

class MfaChallengeRequest(BaseModel):
    user_id: str
    channel: str = "EMAIL"  # SMS | EMAIL | TOTP


class MfaChallengeResponse(BaseModel):
    challenge_id: str
    channel: str
    message: str


class MfaVerifyRequest(BaseModel):
    challenge_id: str
    otp: str


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# ---------------------------------------------------------------------------
# Sessions / Devices
# ---------------------------------------------------------------------------

class SessionOut(BaseModel):
    id: str
    device_fingerprint: str | None
    ip_address: str | None
    user_agent: str | None
    last_used_at: datetime
    expires_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    description: str | None = None
    permissions: list[str] | None = None


class RoleOut(BaseModel):
    id: str
    name: str
    description: str | None
    permissions: list[str]
    etag: str

    model_config = {"from_attributes": True}


class AssignRoleRequest(BaseModel):
    role_ids: list[str]


# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------

class LockoutReleaseResponse(BaseModel):
    user_id: str
    message: str
