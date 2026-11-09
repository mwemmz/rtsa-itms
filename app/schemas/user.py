import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas import validators as V

from app.models.user import UserRole


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    phone_number: str | None = None
    # National Registration Card number, required for self-registration: road
    # reports (and fines for false ones) are tied to it.
    nrc_number: str | None = None
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
    nrc_number: str | None = None
    role: UserRole = UserRole.CITIZEN

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return V.email(v)

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, v: str) -> str:
        return V.person_name(v, "Full name")

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v):
        return V.phone(v)


class AdminUserUpdate(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None

    @field_validator("full_name")
    @classmethod
    def _fn(cls, v):
        return V.person_name(v, "Full name") if v is not None else v

    @field_validator("phone_number")
    @classmethod
    def _ph(cls, v):
        return V.phone(v)

    # Only an administrator can correct an NRC once it is on an account.
    nrc_number: str | None = None
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
    nrc_number: str | None = None
    mfa_enabled: bool = False
    last_login_at: datetime | None = None
    locked_until: datetime | None = None
    email_verified_at: datetime | None = None

    model_config = {"from_attributes": True}


class EmailRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class TokenRequest(BaseModel):
    token: str


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
