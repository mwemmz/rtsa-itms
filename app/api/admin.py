from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_role
from app.models.audit_log import AuditLog
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus
from app.models.notification import NotificationRule
from app.models.payment import Payment
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.notification import NotificationRuleCreate, NotificationRuleResponse
from app.schemas.user import UserResponse
from app.services.audit import log_action

router = APIRouter(prefix="/api/admin", tags=["Admin"])


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    return db.query(User).all()


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(
    user_id: str,
    new_role: UserRole,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = new_role
    db.flush()
    log_action(db, "update_role", "user", str(user.id), f"Set role to {new_role.value}", current_user.id)
    db.commit()
    db.refresh(user)
    return user


@router.get("/audit-logs", response_model=list[dict])
def audit_logs(
    entity_type: str | None = Query(None),
    actor_id: str | None = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    query = db.query(AuditLog)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    logs = query.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit).all()
    return [
        {
            "id": str(log.id),
            "actor_id": str(log.actor_id) if log.actor_id else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "details": log.details,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }
        for log in logs
    ]


@router.get("/reports/summary", response_model=dict)
def report_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    return {
        "vehicles": db.query(Vehicle).count(),
        "drivers": db.query(Driver).count(),
        "challans_total": db.query(Challan).count(),
        "challans_unpaid": db.query(Challan).filter(Challan.status == ChallanStatus.UNPAID).count(),
        "challans_paid": db.query(Challan).filter(Challan.status == ChallanStatus.PAID).count(),
        "revenue_collected": db.query(func.sum(Payment.amount)).filter(Payment.status == "completed").scalar() or 0,
    }


@router.get("/reports/vehicles")
def vehicles_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    from sqlalchemy import text
    result = db.execute(
        text("SELECT status, COUNT(*) FROM vehicles GROUP BY status")
    ).fetchall()
    return {row[0]: row[1] for row in result}


@router.get("/reports/violations")
def violations_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    from sqlalchemy import text
    result = db.execute(
        text("SELECT violation_type, COUNT(*) FROM violations GROUP BY violation_type")
    ).fetchall()
    return {row[0]: row[1] for row in result}


@router.get("/reports/revenue")
def revenue_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    from sqlalchemy import text
    result = db.execute(
        text(
            "SELECT DATE(created_at) AS day, COALESCE(SUM(amount), 0) AS total "
            "FROM payments WHERE status = 'completed' "
            "GROUP BY DATE(created_at) ORDER BY day DESC LIMIT 30"
        )
    ).fetchall()
    return [{"date": str(row[0]), "total": row[1]} for row in result]


# --- Notification rules ---

@router.get("/notification-rules", response_model=list[NotificationRuleResponse])
def list_notification_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    return db.query(NotificationRule).all()


@router.post("/notification-rules", response_model=NotificationRuleResponse, status_code=status.HTTP_201_CREATED)
def create_notification_rule(
    payload: NotificationRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    existing = db.query(NotificationRule).filter(
        NotificationRule.trigger_event == payload.trigger_event
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Rule for this event already exists")
    rule = NotificationRule(**payload.model_dump())
    db.add(rule)
    db.flush()
    log_action(db, "create_rule", "notification_rule", str(rule.id), payload.trigger_event, current_user.id)
    db.commit()
    db.refresh(rule)
    return rule