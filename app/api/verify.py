"""Public check of a fitness certificate: anyone holding the certificate (or its QR code) can confirm it is real."""

import io
from datetime import datetime

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.inspection import FitnessCertificate
from app.models.vehicle import Vehicle

router = APIRouter(prefix="/api/verify", tags=["Verify"])


@router.get("/fitness/{number}")
def verify_fitness(number: str, db: Session = Depends(get_db)):
    cert = db.query(FitnessCertificate).filter(FitnessCertificate.certificate_number == number.strip().upper()).first()
    if cert is None:
        raise HTTPException(status_code=404, detail="No fitness certificate with that number")
    vehicle = db.get(Vehicle, cert.vehicle_id)
    now = datetime.utcnow()
    return {
        "certificate_number": cert.certificate_number,
        "plate": vehicle.registration_number if vehicle else None,
        "vehicle": f"{vehicle.make} {vehicle.model}" if vehicle else None,
        "issued": cert.issued_date.date().isoformat(),
        "expires": cert.expiry_date.date().isoformat(),
        "valid": cert.issued_date <= now <= cert.expiry_date,
    }


@router.get("/fitness/{number}/qr.svg")
def fitness_qr(number: str, request: Request, db: Session = Depends(get_db)):
    if db.query(FitnessCertificate).filter(FitnessCertificate.certificate_number == number.strip().upper()).first() is None:
        raise HTTPException(status_code=404, detail="No fitness certificate with that number")
    url = str(request.base_url).rstrip("/") + f"/api/verify/fitness/{number.strip().upper()}"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0b1d33")
    return Response(buf.getvalue(), media_type="image/svg+xml")
