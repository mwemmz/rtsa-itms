from pydantic import BaseModel


class CitizenFinesSummary(BaseModel):
    total_unpaid: int
    total_amount: int
    challans: list[dict]


class CitizenDashboard(BaseModel):
    vehicles: list[dict]
    licences: list[dict]
    fines: CitizenFinesSummary