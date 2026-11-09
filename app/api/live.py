from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import FIELD_STAFF, require_role
from app.models.user import User
from app.services import live_fleet

router = APIRouter(prefix="/api/live", tags=["Live monitoring"])


@router.get("/vehicles")
def live_vehicles(db: Session = Depends(get_db), current_user: User = Depends(require_role(*FIELD_STAFF))):
    """Every vehicle currently on the simulated live map, with position, speed and compliance facts.

    The movement is simulated (this demo has no GPS units); the vehicles and their documents are real records.
    """
    vehicles = live_fleet.snapshot(db)
    return {
        "simulated": True,
        "vehicles": vehicles,
        "summary": {
            "total": len(vehicles),
            "moving": sum(1 for v in vehicles if v["moving"]),
            "stopped": sum(1 for v in vehicles if not v["moving"]),
            "flagged": sum(1 for v in vehicles if v["state"] != "ok"),
            "off_route": sum(1 for v in vehicles if v["state"] == "offroute"),
        },
    }
