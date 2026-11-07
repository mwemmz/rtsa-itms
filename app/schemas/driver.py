import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.driver import DriverStatus, LicenceClass


class DriverCreate(BaseModel):
    licence_number: str = Field(min_length=3, max_length=50)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    id_number: str = Field(min_length=3, max_length=50)
    date_of_birth: datetime
    phone_number: str | None = None
    email: str | None = None
    address: str | None = None
    licence_class: LicenceClass
    licence_issue_date: datetime
    licence_expiry_date: datetime

    @model_validator(mode="after")
    def _dates_make_sense(self):
        today = datetime.utcnow()
        dob = self.date_of_birth.replace(tzinfo=None)
        if dob > today:
            raise ValueError("Date of birth cannot be in the future")
        if (today - dob).days < 18 * 365 + 4:
            raise ValueError("A driver must be at least 18 years old")
        if self.licence_expiry_date <= self.licence_issue_date:
            raise ValueError("Licence expiry must be after the issue date")
        return self


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
