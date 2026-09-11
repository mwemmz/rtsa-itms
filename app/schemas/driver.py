import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.driver import DriverStatus, LicenceClass


class DriverCreate(BaseModel):
    licence_number: str
    first_name: str
    last_name: str
    id_number: str
    date_of_birth: datetime
    phone_number: str | None = None
    email: str | None = None
    address: str | None = None
    licence_class: LicenceClass
    licence_issue_date: datetime
    licence_expiry_date: datetime


class DriverUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    email: str | None = None
    address: str | None = None
    licence_class: LicenceClass | None = None
    licence_expiry_date: datetime | None = None
    status: DriverStatus | None = None
    restrictions: str | None = None


class DriverResponse(BaseModel):
    id: uuid.UUID
    licence_number: str
    first_name: str
    last_name: str
    id_number: str
    date_of_birth: datetime
    phone_number: str | None
    email: str | None
    address: str | None
    licence_class: LicenceClass
    licence_issue_date: datetime
    licence_expiry_date: datetime
    status: DriverStatus
    restrictions: str | None
    created_at: datetime
    last_updated: datetime

    model_config = {"from_attributes": True}
