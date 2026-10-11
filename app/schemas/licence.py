import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.schemas.fields import clean_nrc

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

from app.models.driver import LicenceClass
from app.models.licence import LicenceApplicationStatus


class LicenceApplicationCreate(BaseModel):
    first_name: Name
    last_name: Name
    # The applicant's NRC; stored in the canonical 123456/78/1 form.
    id_number: str
    date_of_birth: datetime
    requested_class: LicenceClass

    @field_validator("id_number")
    @classmethod
    def _is_an_nrc(cls, value: str) -> str:
        nrc = clean_nrc(value)
        if nrc is None:
            raise ValueError("Enter an NRC number like 123456/78/1")
        return nrc

    @field_validator("date_of_birth")
    @classmethod
    def _born_in_the_past(cls, value: datetime) -> datetime:
        if value.replace(tzinfo=None) >= datetime.utcnow():
            raise ValueError("Date of birth must be in the past")
        return value


class TheoryTestUpdate(BaseModel):
    theory_score: int = Field(ge=0, le=100)


class PracticalTestUpdate(BaseModel):
    practical_score: int = Field(ge=0, le=100)


class LicenceApplicationResponse(BaseModel):
    id: uuid.UUID
    applicant_id: uuid.UUID
    first_name: str
    last_name: str
    id_number: str
    date_of_birth: datetime
    requested_class: LicenceClass
    status: LicenceApplicationStatus
    theory_score: int | None
    practical_score: int | None
    issued_licence_number: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
