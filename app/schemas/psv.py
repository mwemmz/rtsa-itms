import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.psv import PSVOperatorStatus, PSVPermitStatus


class PSVOperatorCreate(BaseModel):
    name: str
    licence_number: str
    contact_person: str
    phone: str
    email: str | None = None
    address: str | None = None


class PSVOperatorResponse(BaseModel):
    id: uuid.UUID
    name: str
    licence_number: str
    contact_person: str
    phone: str
    email: str | None
    address: str | None
    status: PSVOperatorStatus
    registered_date: datetime

    model_config = {"from_attributes": True}


class PSVPermitCreate(BaseModel):
    operator_id: uuid.UUID
    vehicle_id: uuid.UUID
    permit_number: str
    route: str
    issued_date: datetime
    expiry_date: datetime


class PSVPermitResponse(BaseModel):
    id: uuid.UUID
    operator_id: uuid.UUID
    vehicle_id: uuid.UUID
    permit_number: str
    route: str
    issued_date: datetime
    expiry_date: datetime
    status: PSVPermitStatus

    model_config = {"from_attributes": True}