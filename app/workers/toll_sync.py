"""
Toll sync worker — processes offline toll-plaza batches uploaded via
POST /v1/integrations/batch-ingest.
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


def process_toll_event(db, payload: dict) -> None:
    """
    Process a single toll transit event dict, creating a TollRecord.
    Called from the batch-ingest endpoint.
    """
    from app.models.toll import TollRecord, TollPaymentStatus, VehicleClass
    from app.models.vehicles import Vehicle

    plate = payload.get("plate", "") or payload.get("plate_number", "")
    if not plate:
        raise ValueError("Missing plate number in toll event payload")

    plate = plate.upper().strip()
    plaza_code = payload.get("plaza_id") or payload.get("plaza_code")
    vehicle_class_raw = payload.get("vehicle_class", "CLASS_B")
    amount_ngwee = int(payload.get("amount_ngwee", 0))
    transited_at_raw = payload.get("transited_at")
    source_system = payload.get("source_system")

    from datetime import datetime, timezone
    if isinstance(transited_at_raw, str):
        try:
            transited_at = datetime.fromisoformat(transited_at_raw.replace("Z", "+00:00"))
        except ValueError:
            transited_at = datetime.now(timezone.utc)
    else:
        transited_at = datetime.now(timezone.utc)

    try:
        vehicle_class = VehicleClass(vehicle_class_raw)
    except ValueError:
        vehicle_class = VehicleClass.CLASS_B

    # Resolve vehicle
    vehicle = db.query(Vehicle).filter(Vehicle.plate_number == plate).first()

    # Resolve plaza
    plaza_id = None
    if plaza_code:
        from app.models.toll import TollPlaza
        plaza = db.query(TollPlaza).filter(TollPlaza.plaza_code == plaza_code).first()
        if plaza:
            plaza_id = plaza.id

    # Apply rate from settings if no amount provided
    if amount_ngwee == 0:
        from app.models.admin import SystemSetting
        import json as _json
        rate_key = f"TOLL_RATE_{vehicle_class.value}_NGWEE"
        setting = db.query(SystemSetting).filter(SystemSetting.key == rate_key).first()
        amount_ngwee = int(_json.loads(setting.value)) if setting else 5000

    record = TollRecord(
        vehicle_id=vehicle.id if vehicle else None,
        plaza_id=plaza_id,
        plate_number=plate,
        vehicle_class=vehicle_class,
        transited_at=transited_at,
        amount_ngwee=amount_ngwee,
        payment_status=TollPaymentStatus.UNPAID if amount_ngwee > 0 else TollPaymentStatus.WAIVED,
        synced_from_offline=True,
        source_system=source_system,
    )
    db.add(record)
    db.flush()
    logger.info("Toll event: plate=%s plaza=%s amount=%d", plate, plaza_code, amount_ngwee)


def process_toll_batch(source_system: str, events: list[dict]) -> dict:
    """Process a batch of toll events. Returns summary."""
    from app.core.db import SessionLocal
    db = SessionLocal()
    accepted = rejected = 0
    errors = []
    try:
        for idx, event in enumerate(events):
            try:
                process_toll_event(db, event)
                accepted += 1
            except Exception as exc:
                rejected += 1
                errors.append({"index": idx, "error": str(exc)})
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Toll batch error: %s", exc)
    finally:
        db.close()
    logger.info("Toll batch source=%s total=%d accepted=%d rejected=%d",
                source_system, len(events), accepted, rejected)
    return {"total": len(events), "accepted": accepted, "rejected": rejected, "errors": errors}
