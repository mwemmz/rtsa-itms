import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user import UserRole


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    phone_number: str | None = None
    # Public self-registration only ever creates citizens; any other value is
    # rejected. Staff accounts are created by an administrator.
    role: UserRole = UserRole.CITIZEN
    # CAPTCHA (only checked when the "security.captcha_enabled" setting is on).
    # Sandbox provider: captcha_id + captcha_answer. Real provider: captcha_token.
    captcha_id: str | None = None
    captcha_answer: str | None = None
    captcha_token: str | None = None


class AdminUserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    phone_number: str | None = None
    role: UserRole = UserRole.CITIZEN


class AdminUserUpdate(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None
    is_active: bool | None = None
    role: UserRole | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    phone_number: str | None = None
    mfa_enabled: bool = False
    last_login_at: datetime | None = None
    locked_until: datetime | None = None

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None
    mfa_setup_required: bool = False


class LoginRequest(BaseModel):
    email: str
    password: str
    # CAPTCHA (only checked when the "security.captcha_enabled" setting is on).
    # Sandbox provider: captcha_id + captcha_answer. Real provider: captcha_token.
    captcha_id: str | None = None
    captcha_answer: str | None = None
    captcha_token: str | None = None


class MFAVerifyRequest(BaseModel):
    mfa_token: str
    code: str


class MFACodeRequest(BaseModel):
    code: str


class MFADisableRequest(BaseModel):
    password: str
    code: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1)


class SessionResponse(BaseModel):
    id: uuid.UUID
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    is_current: bool = False

    model_config = {"from_attributes": True}


class DeviceResponse(BaseModel):
    id: uuid.UUID
    label: str | None
    user_agent: str | None
    last_ip: str | None
    login_count: int
    is_trusted: bool
    is_blocked: bool
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}
