import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.driver import LicenceClass
from app.models.licence import LicenceApplicationStatus


class LicenceApplicationCreate(BaseModel):
    first_name: str
    last_name: str
    id_number: str
    date_of_birth: datetime
    requested_class: LicenceClass


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
