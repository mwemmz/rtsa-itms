import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.driver import Driver, DriverStatus, LicenceClass
from app.models.licence import LicenceApplication, LicenceApplicationStatus
from app.models.user import User, UserRole
from app.schemas.licence import (
    LicenceApplicationCreate,
    LicenceApplicationResponse,
    PracticalTestUpdate,
    TheoryTestUpdate,
)
from app.services.audit import log_action

router = APIRouter(prefix="/api/licence-applications", tags=["Licence Applications"])


@router.post("/", response_model=LicenceApplicationResponse, status_code=status.HTTP_201_CREATED)
def apply_for_licence(
    payload: LicenceApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = LicenceApplication(
        applicant_id=current_user.id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        id_number=payload.id_number,
        date_of_birth=payload.date_of_birth,
        requested_class=payload.requested_class,
    )
    db.add(app)
    db.flush()
    log_action(db, "apply", "licence_application", str(app.id), f"Applied for {payload.requested_class.value} licence", current_user.id)
    db.commit()
    db.refresh(app)
    return app


@router.get("/", response_model=list[LicenceApplicationResponse])
def list_applications(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    return db.query(LicenceApplication).order_by(LicenceApplication.created_at.desc()).all()


@router.get("/mine", response_model=list[LicenceApplicationResponse])
def my_applications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(LicenceApplication)
        .filter(LicenceApplication.applicant_id == current_user.id)
        .order_by(LicenceApplication.created_at.desc())
        .all()
    )


@router.get("/{application_id}", response_model=LicenceApplicationResponse)
def get_application(
    application_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = db.query(LicenceApplication).filter(LicenceApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.applicant_id != current_user.id and current_user.role not in (UserRole.ADMIN, UserRole.OFFICER):
        raise HTTPException(status_code=403, detail="Not your application")
    return app


@router.post("/{application_id}/theory", response_model=LicenceApplicationResponse)
def record_theory_test(
    application_id: str,
    payload: TheoryTestUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    app = db.query(LicenceApplication).filter(LicenceApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    app.theory_score = payload.theory_score
    if payload.theory_score >= 70:
        app.status = LicenceApplicationStatus.THEORY_TEST_PASSED
    else:
        app.status = LicenceApplicationStatus.THEORY_TEST_FAILED

    db.flush()
    log_action(db, "theory_test", "licence_application", str(app.id), f"Score: {payload.theory_score}", current_user.id)
    db.commit()
    db.refresh(app)
    return app


@router.post("/{application_id}/practical", response_model=LicenceApplicationResponse)
def record_practical_test(
    application_id: str,
    payload: PracticalTestUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    app = db.query(LicenceApplication).filter(LicenceApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    app.practical_score = payload.practical_score
    if payload.practical_score >= 70:
        app.status = LicenceApplicationStatus.PRACTICAL_TEST_PASSED
    else:
        app.status = LicenceApplicationStatus.PRACTICAL_TEST_FAILED

    db.flush()
    log_action(db, "practical_test", "licence_application", str(app.id), f"Score: {payload.practical_score}", current_user.id)
    db.commit()
    db.refresh(app)
    return app


@router.post("/{application_id}/issue", response_model=LicenceApplicationResponse)
def issue_licence(
    application_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    app = db.query(LicenceApplication).filter(LicenceApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    if app.status != LicenceApplicationStatus.PRACTICAL_TEST_PASSED:
        raise HTTPException(status_code=400, detail="Applicant must pass both tests before licence issuance")

    # Check for existing driver with same licence
    licence_number = f"LIC-{secrets.token_hex(4).upper()}"
    driver = Driver(
        licence_number=licence_number,
        first_name=app.first_name,
        last_name=app.last_name,
        id_number=app.id_number,
        date_of_birth=app.date_of_birth,
        licence_class=app.requested_class,
        licence_issue_date=datetime.utcnow(),
        licence_expiry_date=datetime.utcnow().replace(year=datetime.utcnow().year + 5),
    )
    db.add(driver)
    app.issued_licence_number = licence_number
    app.status = LicenceApplicationStatus.ISSUED

    db.flush()
    log_action(db, "issue_licence", "licence_application", str(app.id), f"Issued {licence_number}", current_user.id)
    db.commit()
    db.refresh(app)
    return app
