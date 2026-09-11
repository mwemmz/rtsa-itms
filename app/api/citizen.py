from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.citizen import CitizenDashboard, CitizenFinesSummary
from app.schemas.driver import DriverResponse
from app.schemas.enforcement import ChallanResponse
from app.schemas.vehicle import VehicleResponse

router = APIRouter(prefix="/api/citizen", tags=["Citizen Portal"])


@router.get("/dashboard", response_model=CitizenDashboard)
def citizen_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicles = (
        db.query(Vehicle)
        .filter(Vehicle.owner_id_number == current_user.email.split("@")[0])
        .all()
    )
    # Fallback: also show vehicles linked by identification
    if not vehicles:
        vehicles = db.query(Vehicle).filter(Vehicle.owner_name.ilike(f"%{current_user.full_name}%")).all()

    licences = db.query(Driver).filter(Driver.id_number == current_user.email.split("@")[0]).all()
    if not licences:
        licences = db.query(Driver).filter(Driver.email == current_user.email).all()

    vehicle_ids = [v.id for v in vehicles]
    challans = (
        db.query(Challan)
        .filter(
            Challan.vehicle_id.in_(vehicle_ids) if vehicle_ids else Challan.vehicle_id is None,
        )
        .all()
    )
    unpaid = [c for c in challans if c.status == ChallanStatus.UNPAID]
    total_amount = sum(c.penalty_amount for c in unpaid)

    return CitizenDashboard(
        vehicles=[VehicleResponse.model_validate(v).model_dump() for v in vehicles],
        licences=[DriverResponse.model_validate(l).model_dump() for l in licences],
        fines=CitizenFinesSummary(
            total_unpaid=len(unpaid),
            total_amount=total_amount,
            challans=[ChallanResponse.model_validate(c).model_dump() for c in challans],
        ),
    )


@router.get("/my-vehicles", response_model=list[VehicleResponse])
def my_vehicles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicles = (
        db.query(Vehicle)
        .filter(Vehicle.owner_name.ilike(f"%{current_user.full_name}%"))
        .all()
    )
    return vehicles


@router.get("/my-fines", response_model=list[ChallanResponse])
def my_fines(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicles = db.query(Vehicle).filter(Vehicle.owner_name.ilike(f"%{current_user.full_name}%")).all()
    vehicle_ids = [v.id for v in vehicles]
    challans = (
        db.query(Challan)
        .filter(Challan.vehicle_id.in_(vehicle_ids) if vehicle_ids else Challan.vehicle_id.is_(None))
        .all()
    )
    return challans