"""Fill the database with believable Zambian demo data for testing and the demo.

    python scripts/seed_demo.py            # add the demo data (once)
    python scripts/seed_demo.py --reset    # remove what this script added, then add it again

Everything goes in through the same code the app uses (fines, payments, tolls), so
the numbers on the admin dashboards are real totals, not typed-in ones. Dates are
spread over the last six months so the monthly charts have a shape.

Every account this script makes has the password  Demo12345!x  and an email ending
@demo.rtsa.gov.zm (staff) or @demo.example.zm (citizens), which is also how --reset
finds them. The seeded random generator makes the data the same every run.
"""

import argparse
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_  # noqa: E402

from app.api.enforcement import VIOLATION_PENALTIES, create_challan_for_violation  # noqa: E402
from app.api.toll import _process_toll_event  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.accident import Accident, AccidentSeverity, AccidentStatus, AccidentVehicle  # noqa: E402
from app.models.driver import Driver, DriverStatus, LicenceClass  # noqa: E402
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType  # noqa: E402
from app.models.inspection import FitnessCertificate, Inspection, InspectionResult  # noqa: E402
from app.models.insurance import Insurance  # noqa: E402
from app.models.anpr import ANPREvent  # noqa: E402
from app.models.driver_report import DriverReport  # noqa: E402
from app.models.platform import AgencyClient, IntegrationLog, ReconciliationRun  # noqa: E402
from app.models.licence import LicenceApplication, LicenceApplicationStatus  # noqa: E402
from app.models.payment import Payment, PaymentStatus, PaymentType  # noqa: E402
from app.models.psv import (  # noqa: E402
    PSVCorridor, PSVOperator, PSVOperatorStatus, PSVPermit, PSVPermitStatus, PSVRouteAlert,
)
from app.models.road_network import (  # noqa: E402
    IncidentSeverity, IncidentType, Road, RoadIncident,
)
from app.models.toll import TollTransaction  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.models.vehicle import Vehicle, VehicleStatus  # noqa: E402
from app.services.money import zmw  # noqa: E402
from app.services.payments import complete_payment, create_payment, fail_payment  # noqa: E402

PASSWORD = "Demo12345!x"
STAFF_DOMAIN = "demo.rtsa.gov.zm"
CITIZEN_DOMAIN = "demo.example.zm"
NOW = datetime.utcnow().replace(microsecond=0)
rng = random.Random(2026)

FIRST_M = ["Chanda", "Mwansa", "Kelvin", "Bwalya", "Mulenga", "Chisanga", "Kabwe", "Brian", "Patrick", "Joseph",
           "Lackson", "Mukuka", "Chileshe", "Henry", "Wilson", "Francis", "Nelson", "Stanley", "Emmanuel", "Mumba",
           "Webster", "Derrick", "Kondwani", "Isaac"]
FIRST_F = ["Chanda", "Mutinta", "Natasha", "Thandiwe", "Esther", "Namukolo", "Mercy", "Grace", "Chola", "Bwalya",
           "Memory", "Precious", "Tamara", "Mwiza", "Lubona", "Regina", "Loveness", "Cynthia", "Beatrice", "Related"]
FIRST_F[-1] = "Mapenzi"
LAST = ["Mwale", "Banda", "Phiri", "Zulu", "Tembo", "Lungu", "Mulenga", "Chanda", "Sakala", "Mumba", "Daka", "Musonda",
        "Kapambwe", "Ngoma", "Simwanza", "Chilufya", "Mutale", "Hamusonde", "Siame", "Mwamba", "Kunda", "Nyirenda",
        "Chibwe", "Mbewe", "Shamilimo", "Nkandu", "Sikazwe", "Kalaba", "Bwalya", "Mukuka"]

MAKES = [
    ("Toyota", "Hilux", 22), ("Toyota", "Corolla", 18), ("Toyota", "Premio", 8), ("Toyota", "Vitz", 6),
    ("Toyota", "Fortuner", 6), ("Toyota", "Hiace", 10), ("Toyota", "Land Cruiser", 4), ("Nissan", "NP300", 8),
    ("Nissan", "Hardbody", 4), ("Nissan", "X-Trail", 5), ("Mazda", "Demio", 5), ("Mazda", "BT-50", 3),
    ("Isuzu", "D-Max", 7), ("Honda", "Fit", 5), ("Suzuki", "Swift", 4), ("Ford", "Ranger", 5),
    ("Mitsubishi", "Canter", 3), ("Higer", "Coaster Bus", 3), ("FAW", "Truck", 3), ("Scania", "R-Series", 2),
]
COLOURS = ["White", "Silver", "Black", "Blue", "Grey", "Red", "Green", "Maroon"]
PLATE_LETTERS = ["ALF", "BAL", "BAM", "BAK", "BAH", "BAG", "BAC", "ADD", "AEE", "BBA", "BCD"]

OFFENCE_WEIGHTS = [
    (ViolationType.SPEEDING, 26), (ViolationType.USING_PHONE, 12), (ViolationType.REAR_SEAT_BELT, 10),
    (ViolationType.ILLEGAL_PARKING, 10), (ViolationType.EXPIRED_ROAD_TAX, 8), (ViolationType.NO_INSURANCE, 8),
    (ViolationType.RUNNING_RED_LIGHT, 6), (ViolationType.EXPIRED_FITNESS, 5), (ViolationType.MISSING_NUMBER_PLATES, 4),
    (ViolationType.OVERLOADING, 4), (ViolationType.UNROADWORTHY, 3), (ViolationType.DRIVING_WITHOUT_LICENCE, 3),
    (ViolationType.DRUNK_DRIVING, 3), (ViolationType.RECKLESS_DRIVING, 3), (ViolationType.ILLEGAL_MODIFICATION, 1),
    (ViolationType.NO_PSV_PERMIT, 1),
]
LOCATIONS = [
    ("Great East Road, near UNZA", -15.3945, 28.3350), ("Cairo Road, Lusaka CBD", -15.4167, 28.2833),
    ("Independence Avenue, Lusaka", -15.4125, 28.2870), ("Kafue Road, Chilanga", -15.5500, 28.2700),
    ("Great North Road, Kabwe", -14.4469, 28.4464), ("Addis Ababa Drive", -15.4010, 28.3090),
    ("Mumbwa Road, Lusaka", -15.4060, 28.2400), ("Lumumba Road, Lusaka", -15.4220, 28.3000),
    ("Chirundu Road", -15.9500, 28.8500), ("Kitwe-Ndola Dual Carriageway", -12.9000, 28.6500),
    ("Chingola Road, Chililabombwe", -12.3600, 27.8200), ("Livingstone-Maramba Road", -17.8500, 25.8600),
    ("Great East Road, Chongwe", -15.3300, 28.6800), ("Mongu-Lusaka Road, Kafue", -15.7700, 28.1800),
]
GATES = ["Kafue Toll Plaza", "Chirundu Toll Plaza", "Katuba Toll Plaza", "Shimabala Toll Plaza", "Kapiri Mposhi Toll Plaza"]
# Toll fees in ngwee (K30 = 3000), like every amount the system stores.
GATE_FEE = {"Kafue Toll Plaza": 3000, "Chirundu Toll Plaza": 5000, "Katuba Toll Plaza": 4000,
            "Shimabala Toll Plaza": 3000, "Kapiri Mposhi Toll Plaza": 6000}
INSURERS = ["ZSIC General", "Madison General", "Professional Insurance", "Hollard Zambia", "Mayfair Insurance"]
CENTRES = ["RTSA Lusaka Fitness Centre", "RTSA Kitwe Fitness Centre", "RTSA Ndola Fitness Centre", "RTSA Livingstone Fitness Centre"]
PSV_OPERATORS = [
    ("Mazhandu Family Bus Services", "Lusaka - Kitwe", "Great North Road"),
    ("Power Tools Transport", "Lusaka - Livingstone", "Mongu-Lusaka Road"),
    ("Shalom Coaches", "Lusaka - Chipata", "Great East Road"),
    ("Kamwala Minibus Association", "Kamwala - City Market", "Cairo Road"),
    ("Soweto Express Taxis", "Soweto Market - Kalingalinga", "Independence Avenue"),
]
ACCIDENT_TEXT = [
    "Head-on collision on a bend; both drivers taken to hospital", "Rear-end collision at a traffic light",
    "Minibus overturned after a tyre burst", "Pedestrian knocked down at a crossing",
    "Truck jackknifed on a wet road", "Two cars collided at an uncontrolled junction",
    "Vehicle left the road while avoiding a goat", "Motorcycle hit by a car overtaking on a solid line",
]


def money_kwacha(low: int, high: int) -> int:
    return rng.randint(low, high)


def when(days_back_max: int, days_back_min: int = 0) -> datetime:
    """A random moment in the last N days, mostly in daylight hours."""
    base = NOW - timedelta(days=rng.uniform(days_back_min, days_back_max))
    return base.replace(hour=rng.choice([6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21]),
                        minute=rng.randint(0, 59), second=0)


def pick_weighted(pairs):
    total = sum(w for _, w in pairs)
    r = rng.uniform(0, total)
    for item, w in pairs:
        r -= w
        if r <= 0:
            return item
    return pairs[-1][0]


def reset(db) -> None:
    """Delete only what this script created. Parent rows go last."""
    users = db.query(User).filter(or_(User.email.like(f"%@{STAFF_DOMAIN}"), User.email.like(f"%@{CITIZEN_DOMAIN}"))).all()
    uids = [u.id for u in users]
    vehicles = db.query(Vehicle).filter(Vehicle.user_id.in_(uids)).all() if uids else []
    vids = [v.id for v in vehicles]
    from app.models.platform import PaymentEvent
    violations = db.query(Violation).filter(or_(Violation.vehicle_id.in_(vids), Violation.user_id.in_(uids))).all() if vids else []
    viol_ids = [v.id for v in violations]
    challans = db.query(Challan).filter(Challan.violation_id.in_(viol_ids)).all() if viol_ids else []
    ch_ids = [c.id for c in challans]
    tolls = db.query(TollTransaction).filter(TollTransaction.vehicle_id.in_(vids)).all() if vids else []
    toll_ids = [t.id for t in tolls]
    pay_ids = [p.id for p in db.query(Payment).filter(Payment.related_entity_id.in_(ch_ids + toll_ids)).all()] if (ch_ids or toll_ids) else []
    if pay_ids:
        db.query(PaymentEvent).filter(PaymentEvent.payment_id.in_(pay_ids)).delete(synchronize_session=False)
        db.query(Payment).filter(Payment.id.in_(pay_ids)).delete(synchronize_session=False)
    if ch_ids:
        db.query(Challan).filter(Challan.id.in_(ch_ids)).delete(synchronize_session=False)
    if viol_ids:
        db.query(Violation).filter(Violation.id.in_(viol_ids)).delete(synchronize_session=False)
    if toll_ids:
        db.query(TollTransaction).filter(TollTransaction.id.in_(toll_ids)).delete(synchronize_session=False)
    if vids:
        permits = db.query(PSVPermit).filter(PSVPermit.vehicle_id.in_(vids)).all()
        pm_ids = [p.id for p in permits]
        if pm_ids:
            db.query(PSVCorridor).filter(PSVCorridor.permit_id.in_(pm_ids)).delete(synchronize_session=False)
            db.query(PSVPermit).filter(PSVPermit.id.in_(pm_ids)).delete(synchronize_session=False)
        if pm_ids:
            db.query(PSVRouteAlert).filter(PSVRouteAlert.permit_id.in_(pm_ids)).delete(synchronize_session=False)
        db.query(ANPREvent).filter(ANPREvent.camera_id.like("DEMO-%")).delete(synchronize_session=False)
        db.query(AccidentVehicle).filter(AccidentVehicle.vehicle_id.in_(vids)).delete(synchronize_session=False)
        db.query(FitnessCertificate).filter(FitnessCertificate.vehicle_id.in_(vids)).delete(synchronize_session=False)
        db.query(Inspection).filter(Inspection.vehicle_id.in_(vids)).delete(synchronize_session=False)
        db.query(Insurance).filter(Insurance.vehicle_id.in_(vids)).delete(synchronize_session=False)
        db.query(Vehicle).filter(Vehicle.id.in_(vids)).delete(synchronize_session=False)
    if uids:
        db.query(DriverReport).filter(DriverReport.reporter_id.in_(uids)).delete(synchronize_session=False)
    db.query(IntegrationLog).filter(IntegrationLog.agency_name.like("[demo]%")).delete(synchronize_session=False)
    db.query(AgencyClient).filter(AgencyClient.name.like("[demo]%")).delete(synchronize_session=False)
    db.query(ReconciliationRun).filter(ReconciliationRun.report.like("[demo]%")).delete(synchronize_session=False)
    db.query(PSVOperator).filter(PSVOperator.licence_number.like("DEMO-%")).delete(synchronize_session=False)
    db.query(Accident).filter(Accident.description.like("[demo]%")).delete(synchronize_session=False)
    db.query(RoadIncident).filter(RoadIncident.description.like("[demo]%")).delete(synchronize_session=False)
    if uids:
        db.query(LicenceApplication).filter(LicenceApplication.applicant_id.in_(uids)).delete(synchronize_session=False)
        db.query(Driver).filter(Driver.user_id.in_(uids)).delete(synchronize_session=False)
        _detach_users(db, uids)
        db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.commit()
    print("Removed earlier demo data")


def _detach_users(db, uids) -> None:
    """Clear everything that still points at the demo users: delete rows they own, blank optional links."""
    from app.core.database import Base
    for table in reversed(Base.metadata.sorted_tables):
        for col in table.columns:
            if any(fk.column.table.name == "users" for fk in col.foreign_keys):
                cond = col.in_(uids)
                if col.nullable and not col.primary_key:
                    db.execute(table.update().where(cond).values({col.name: None}))
                else:
                    db.execute(table.delete().where(cond))


def make_users(db):
    pw = hash_password(PASSWORD)
    verified = NOW
    staff = []
    for key, role, name in [
        ("officer.chanda", UserRole.OFFICER, "Inspector Chanda Mulenga"),
        ("officer.mwansa", UserRole.OFFICER, "Sergeant Mwansa Phiri"),
        ("officer.bwalya", UserRole.OFFICER, "Constable Bwalya Tembo"),
        ("officer.thandiwe", UserRole.OFFICER, "Inspector Thandiwe Zulu"),
        ("toll.kafue", UserRole.TOLL_OPERATOR, "Kelvin Sakala (Kafue Plaza)"),
        ("toll.chirundu", UserRole.TOLL_OPERATOR, "Mutinta Hamusonde (Chirundu Plaza)"),
        ("admin.finance", UserRole.ADMIN, "Esther Kapambwe (Finance)"),
    ]:
        u = User(email=f"{key}@{STAFF_DOMAIN}", hashed_password=pw, full_name=name, role=role,
                 phone_number=f"+26097{rng.randint(1000000, 9999999)}", email_verified_at=verified)
        db.add(u)
        staff.append(u)
    citizens = []
    used = set()
    for i in range(30):
        female = rng.random() < 0.42
        first = rng.choice(FIRST_F if female else FIRST_M)
        last = rng.choice(LAST)
        name = f"{first} {last}"
        if name in used:
            name = f"{first} {rng.choice(LAST)}"
        used.add(name)
        nrc = f"{rng.randint(100000, 999999)}/{rng.choice([10, 11, 12, 13, 14, 15, 16, 17, 18])}/1"
        u = User(email=f"{first.lower()}.{last.lower()}{i}@{CITIZEN_DOMAIN}", hashed_password=pw, full_name=name,
                 role=UserRole.CITIZEN, phone_number=f"+260{rng.choice(['97', '96', '95', '77', '76'])}{rng.randint(1000000, 9999999)}",
                 nrc_number=nrc, email_verified_at=verified)
        db.add(u)
        citizens.append(u)
    db.flush()
    return staff, citizens


def make_drivers_and_vehicles(db, citizens):
    drivers, vehicles = [], []
    makes = [(m, mo) for m, mo, w in MAKES for _ in range(w)]
    plate_seen = set()
    for idx, u in enumerate(citizens):
        first, _, last = u.full_name.partition(" ")
        nrc_digits = u.nrc_number.replace("/", "")
        # 27 of 30 citizens hold a licence; 3 never got one (they are the "driving without licence" cases).
        if idx < 27:
            issue = NOW - timedelta(days=rng.randint(200, 3000))
            expiry = issue + timedelta(days=365 * 5)
            if idx in (3, 11):
                expiry = NOW - timedelta(days=rng.randint(10, 120))  # lapsed licence
            d = Driver(
                user_id=u.id, licence_number=f"ZM{rng.randint(10000000, 99999999)}", first_name=first, last_name=last,
                id_number=nrc_digits, date_of_birth=NOW - timedelta(days=365 * rng.randint(21, 62) + rng.randint(0, 300)),
                phone_number=u.phone_number, email=u.email, address=rng.choice(
                    ["Kabulonga, Lusaka", "Woodlands, Lusaka", "Matero, Lusaka", "Chilenje, Lusaka", "Kalingalinga, Lusaka",
                     "Northmead, Lusaka", "Riverside, Kitwe", "Kansenshi, Ndola", "Chelstone, Lusaka", "Garden, Lusaka"]),
                licence_class=rng.choice([LicenceClass.B, LicenceClass.B, LicenceClass.B, LicenceClass.C, LicenceClass.D]),
                licence_issue_date=issue, licence_expiry_date=expiry,
                status=DriverStatus.EXPIRED if expiry < NOW else DriverStatus.ACTIVE,
            )
            if idx == 7:
                d.status = DriverStatus.SUSPENDED
            db.add(d)
            drivers.append(d)
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            make, model = rng.choice(makes)
            while True:
                plate = f"{rng.choice(PLATE_LETTERS)} {rng.randint(1000, 9999)}"
                if plate not in plate_seen:
                    plate_seen.add(plate)
                    break
            v = Vehicle(
                user_id=u.id, registration_number=plate, owner_name=u.full_name, owner_id_number=nrc_digits,
                make=make, model=model, year=rng.randint(2004, 2025), color=rng.choice(COLOURS),
                engine_number=f"ENG{rng.randint(10**7, 10**8 - 1)}", chassis_number=f"CH{uuid.uuid4().hex[:14].upper()}",
                fastag_id=f"FT-{rng.randint(10**8, 10**9 - 1)}" if rng.random() < 0.55 else None,
                registration_date=NOW - timedelta(days=rng.randint(60, 2500)),
            )
            db.add(v)
            vehicles.append(v)
    # A few vehicles the register treats specially.
    for v, reason in [(vehicles[5], "Reported stolen, Lusaka Central Police Station"),
                      (vehicles[19], "Used in a hit-and-run; held for investigation")]:
        v.is_blacklisted = True
        v.blacklist_reason = reason
    db.flush()
    return drivers, vehicles


def make_documents(db, vehicles, officers):
    """Insurance and fitness. About 70% fully compliant, the rest each missing or expired something."""
    state = {}
    for i, v in enumerate(vehicles):
        roll = rng.random()
        insured = roll > 0.16
        fit = rng.random() > 0.2
        state[v.id] = {"insured": insured, "fit": fit}
        if insured:
            # Some policies run out in the next 30 days so the expiry reminders have something to show.
            soon = rng.random() < 0.15
            start = NOW - timedelta(days=rng.randint(30, 330)) if not soon else NOW - timedelta(days=rng.randint(340, 360))
            db.add(Insurance(vehicle_id=v.id, provider=rng.choice(INSURERS), policy_number=f"POL-{rng.randint(10**6, 10**7 - 1)}",
                             start_date=start, end_date=start + timedelta(days=365), is_active=True))
        elif rng.random() < 0.6:
            start = NOW - timedelta(days=rng.randint(400, 700))
            db.add(Insurance(vehicle_id=v.id, provider=rng.choice(INSURERS), policy_number=f"POL-{rng.randint(10**6, 10**7 - 1)}",
                             start_date=start, end_date=start + timedelta(days=365), is_active=False))
        sched = NOW - timedelta(days=rng.randint(20, 600))
        if fit:
            ins = Inspection(vehicle_id=v.id, inspection_centre=rng.choice(CENTRES), scheduled_date=sched,
                             result=InspectionResult.PASSED, inspected_by=rng.choice(officers).full_name, findings="Roadworthy")
            db.add(ins)
            db.flush()
            issued = NOW - timedelta(days=rng.randint(10, 300))
            db.add(FitnessCertificate(inspection_id=ins.id, vehicle_id=v.id, certificate_number=f"FC-{rng.randint(10**6, 10**7 - 1)}",
                                      issued_date=issued, expiry_date=issued + timedelta(days=365)))
        else:
            roll2 = rng.random()
            if roll2 < 0.5:  # failed inspection, no certificate
                db.add(Inspection(vehicle_id=v.id, inspection_centre=rng.choice(CENTRES), scheduled_date=sched,
                                  result=InspectionResult.FAILED, inspected_by=rng.choice(officers).full_name,
                                  findings=rng.choice(["Worn tyres and faulty brake lights", "Cracked windscreen, defective handbrake",
                                                       "Excessive exhaust smoke"])))
            elif roll2 < 0.8:  # certificate has lapsed
                ins = Inspection(vehicle_id=v.id, inspection_centre=rng.choice(CENTRES), scheduled_date=sched,
                                 result=InspectionResult.PASSED, inspected_by=rng.choice(officers).full_name, findings="Roadworthy")
                db.add(ins)
                db.flush()
                issued = NOW - timedelta(days=rng.randint(400, 700))
                db.add(FitnessCertificate(inspection_id=ins.id, vehicle_id=v.id, certificate_number=f"FC-{rng.randint(10**6, 10**7 - 1)}",
                                          issued_date=issued, expiry_date=issued + timedelta(days=365)))
    db.flush()
    return state


def make_fines(db, vehicles, drivers, officers, citizens):
    """About 170 fines over six months, mostly paid, some outstanding, a few disputed or failed."""
    by_user = {u.id: u for u in citizens}
    driver_by_user = {d.user_id: d for d in drivers}
    stats = {"paid": 0, "unpaid": 0, "disputed": 0, "failed_then_paid": 0}
    for n in range(170):
        v = rng.choice(vehicles)
        owner = by_user[v.user_id]
        vt = pick_weighted(OFFENCE_WEIGHTS)
        loc = rng.choice(LOCATIONS)
        ts = when(180, 1)
        speed = limit = device = None
        if vt == ViolationType.SPEEDING:
            limit = rng.choice([50, 60, 80, 100])
            speed = limit + rng.randint(8, 45)
            device = rng.choice(["LTI 20-20 laser", "Truvelo radar", "Mobile speed camera 02"])
        drv = driver_by_user.get(v.user_id) if vt.category in ("driver", "both") else None
        officer = rng.choice(officers)
        viol = Violation(
            vehicle_id=v.id, driver_id=drv.id if drv else None, violation_type=vt, location=loc[0],
            latitude=loc[1] + rng.uniform(-0.01, 0.01), longitude=loc[2] + rng.uniform(-0.01, 0.01), gps_accuracy_m=rng.choice([5, 8, 12]),
            speed_kmh=speed, speed_limit_kmh=limit, speed_device=device, timestamp=ts,
            description=None, recorded_by=officer.id, liable_party=vt.liable_party,
        )
        db.add(viol)
        db.flush()
        viol.created_at = ts
        ch = create_challan_for_violation(db, viol, officer.id)
        ch.created_at = ts
        ch.due_date = ts + timedelta(days=14)
        age_days = (NOW - ts).days
        roll = rng.random()
        # Older fines are far more likely to have been paid.
        pay_chance = 0.9 if age_days > 60 else 0.7 if age_days > 20 else 0.45
        if roll < pay_chance:
            gateway = rng.choices(["mobile_money", "sandbox"], weights=[7, 3])[0]
            if rng.random() < 0.07:  # one failed attempt first
                bad, _ = create_payment(db, owner, PaymentType.FINE, ch.id, gateway="sandbox_decline")
                bad.created_at = ts + timedelta(days=rng.randint(1, 3))
                stats["failed_then_paid"] += 1
            pay, _ = create_payment(db, owner, PaymentType.FINE, ch.id, gateway=gateway)
            if pay.status == PaymentStatus.PENDING:  # mobile money waits for the gateway; settle it as the webhook would
                complete_payment(db, pay)
            paid_at = ts + timedelta(days=rng.randint(0, 13), hours=rng.randint(1, 8))
            if paid_at > NOW:
                paid_at = NOW - timedelta(hours=rng.randint(1, 20))
            pay.created_at = pay.paid_at = paid_at
            stats["paid"] += 1
        elif roll < pay_chance + 0.03:
            ch.status = ChallanStatus.DISPUTED
            stats["disputed"] += 1
        else:
            stats["unpaid"] += 1
    db.flush()
    return stats


def make_nomination_demo(db, vehicles, drivers, officers, citizens):
    """Two live "who was driving?" cases, so the feature can be shown the moment a demo citizen signs in.

    Older fines were issued long ago, so any request to name a driver on those is closed first.
    """
    from app.models.enforcement import Challan as _Challan
    from app.services.nomination import nominate

    db.query(_Challan).filter(_Challan.nomination_status == "requested").update({"nomination_status": "none"})
    # The six months of bulk fines predate demerit points: clear the points (and any suspension they caused)
    # so the demo starts clean, then give one citizen a realistic near-limit record below.
    from app.models.driver import DriverStatus
    from app.models.enforcement import DemeritPoint

    db.query(DemeritPoint).delete()
    for d in drivers:
        if d.status == DriverStatus.SUSPENDED and d.licence_expiry_date > NOW:
            d.status = DriverStatus.ACTIVE
    db.flush()
    driver_of = {d.user_id: d for d in drivers}
    owner_a, owner_b = citizens[0], citizens[1]
    officer = officers[0]

    def fine_for(owner, minutes_ago):
        v = next(x for x in vehicles if x.user_id == owner.id)
        viol = Violation(
            vehicle_id=v.id, driver_id=None, violation_type=ViolationType.USING_PHONE, location="Cairo Road, Lusaka",
            latitude=-15.4167, longitude=28.2833, gps_accuracy_m=8, evidence_source="cctv",
            timestamp=NOW - timedelta(minutes=minutes_ago), recorded_by=officer.id, liable_party="driver",
            description="Driver seen holding a phone (camera frame)",
        )
        db.add(viol)
        db.flush()
        return create_challan_for_violation(db, viol, officer.id)

    # A: asks citizens[0], the owner, who was driving.
    fine_for(owner_a, 90)
    # B: citizens[1] already named citizens[0] as the driver, so citizens[0] is also asked to accept or deny.
    ch = fine_for(owner_b, 240)
    target = driver_of.get(owner_a.id)
    if target is not None:
        nominate(db, owner_b, ch.id, target.licence_number, "I lent the car to my cousin that afternoon")
    # citizens[2]: points near the limit (one more offence suspends the licence) and a vehicle reported stolen.
    from app.models.vehicle import VehicleStatus
    from app.services.demerits import award_points

    owner_c = citizens[2]
    drv_c = driver_of.get(owner_c.id)
    if drv_c is not None:
        drv_c.licence_expiry_date = NOW + timedelta(days=400)
        drv_c.status = DriverStatus.ACTIVE
        for vt, mins in ((ViolationType.REAR_SEAT_BELT, 60 * 24 * 40), (ViolationType.RUNNING_RED_LIGHT, 60 * 24 * 22), (ViolationType.USING_PHONE, 60 * 24 * 9)):
            v = next(x for x in vehicles if x.user_id == owner_c.id)
            viol = Violation(vehicle_id=v.id, driver_id=drv_c.id, violation_type=vt, location="Independence Avenue, Lusaka",
                             timestamp=NOW - timedelta(minutes=mins), recorded_by=officer.id, liable_party="driver")
            db.add(viol)
            db.flush()
            create_challan_for_violation(db, viol, officer.id)
    stolen = next((x for x in vehicles if x.user_id == citizens[3].id), None)
    if stolen is not None:
        stolen.status = VehicleStatus.STOLEN
        stolen.stolen_reported_at = NOW - timedelta(days=2)
        stolen.police_report_number = "CR-4471/26"
    return {"asks_owner": owner_a.email, "names_driver": owner_a.email if target else None,
            "near_demerit_limit": owner_c.email, "stolen_vehicle_owner": citizens[3].email}


def make_tolls(db, vehicles, toll_ops, citizens):
    by_user = {u.id: u for u in citizens}
    tagged = [v for v in vehicles if v.fastag_id]
    from app.services.compliance import check_vehicle_compliance
    clean = [v for v in vehicles if check_vehicle_compliance(db, v).compliant]
    count = 0
    for _ in range(220):
        # Most traffic through a toll gate is in order; about one in five is stopped.
        v = rng.choice(clean) if clean and rng.random() < 0.93 else rng.choice(vehicles)
        gate = rng.choice(GATES)
        fee = GATE_FEE[gate] * (3 if v.model in ("Truck", "R-Series", "Canter", "Coaster Bus") else 1)
        ts = when(120, 0)
        mlff = rng.random() < 0.12
        method_tag = v in tagged and rng.random() < 0.6
        resp = _process_toll_event(
            db, None if method_tag else v.registration_number, gate, ts, fee, rng.choice(toll_ops),
            tag_id=v.fastag_id if method_tag else None, lane_mode="mlff" if mlff else "barrier",
        )
        tx = db.query(TollTransaction).filter(TollTransaction.vehicle_id == v.id).order_by(TollTransaction.created_at.desc()).first()
        tx.timestamp = ts
        tx.created_at = ts
        if mlff and not tx.is_paid and (NOW - ts).days > 3 and rng.random() < 0.7:
            owner = by_user[v.user_id]
            pay, _ = create_payment(db, owner, PaymentType.TOLL, tx.id, gateway="sandbox")
            pay.created_at = pay.paid_at = ts + timedelta(days=rng.randint(1, 6))
        count += 1
    db.flush()
    # Auto-generated challans from flagged tolls need their dates pulled back to the toll date too.
    for tx in db.query(TollTransaction).filter(TollTransaction.flagged_issues.isnot(None)).all():
        for ch in db.query(Challan).filter(Challan.vehicle_id == tx.vehicle_id, Challan.created_at > tx.timestamp).all():
            if (ch.created_at - tx.timestamp).total_seconds() < 86400 * 400 and ch.created_at.date() == NOW.date():
                ch.created_at = tx.timestamp
                ch.due_date = tx.timestamp + timedelta(days=14)
                v = db.get(Violation, ch.violation_id)
                if v:
                    v.timestamp = v.created_at = tx.timestamp
    return count


def make_psv(db, vehicles, officers):
    psv_vehicles = [v for v in vehicles if v.model in ("Hiace", "Coaster Bus", "Canter", "Corolla", "Vitz")][:12]
    ops = []
    for i, (name, route, road) in enumerate(PSV_OPERATORS):
        op = PSVOperator(name=name, licence_number=f"DEMO-PSV-{100 + i}", contact_person=f"{rng.choice(FIRST_M)} {rng.choice(LAST)}",
                         phone=f"+26096{rng.randint(1000000, 9999999)}", email=f"info{i}@demo-operator.example.zm",
                         address=rng.choice(["Plot 14, Lumumba Road, Lusaka", "Kamwala South, Lusaka", "Main Street, Kitwe", "Soweto Market, Lusaka"]),
                         status=PSVOperatorStatus.SUSPENDED if i == 4 else PSVOperatorStatus.ACTIVE,
                         registered_date=NOW - timedelta(days=rng.randint(200, 1500)))
        db.add(op)
        ops.append((op, route, road))
    db.flush()
    n = 0
    for i, v in enumerate(psv_vehicles):
        op, route, _ = ops[i % len(ops)]
        issued = NOW - timedelta(days=rng.randint(30, 400))
        expiry = issued + timedelta(days=365)
        status = PSVPermitStatus.ACTIVE
        if expiry < NOW:
            status = PSVPermitStatus.EXPIRED
        if i == 3:
            status = PSVPermitStatus.SUSPENDED
        p = PSVPermit(operator_id=op.id, vehicle_id=v.id, permit_number=f"PSV-{2026}-{1000 + i}", route=route,
                      issued_date=issued, expiry_date=expiry, status=status)
        db.add(p)
        db.flush()
        if i % 2 == 0:
            import json
            db.add(PSVCorridor(permit_id=p.id, waypoints=json.dumps([
                {"lat": -15.4167, "lon": 28.2833}, {"lat": -15.3945, "lon": 28.3350}, {"lat": -15.3300, "lon": 28.6800}]),
                buffer_m=500, updated_by=officers[0].id))
        n += 1
    return n


def make_accidents(db, vehicles, drivers, officers):
    roads = db.query(Road).all()
    n = 0
    for _ in range(28):
        sev = rng.choices([AccidentSeverity.MINOR, AccidentSeverity.SERIOUS, AccidentSeverity.FATAL], weights=[60, 30, 10])[0]
        loc = rng.choice(LOCATIONS)
        ts = when(180, 1)
        status = rng.choices(list(AccidentStatus), weights=[15, 20, 25, 40])[0]
        acc = Accident(location=loc[0], occurred_at=ts, severity=sev, status=status,
                       description="[demo] " + rng.choice(ACCIDENT_TEXT), road_id=rng.choice(roads).id if roads else None,
                       reported_by=rng.choice(officers).id)
        db.add(acc)
        db.flush()
        acc.created_at = ts
        for v in rng.sample(vehicles, rng.choice([1, 2, 2, 3])):
            d = next((x for x in drivers if x.user_id == v.user_id), None)
            db.add(AccidentVehicle(accident_id=acc.id, vehicle_id=v.id, driver_id=d.id if d else None,
                                   plate_number=v.registration_number, role=rng.choice(["at fault", "involved", "involved"])))
        n += 1
    return n


def make_incidents(db):
    roads = db.query(Road).all()
    if not roads:
        return 0
    items = [
        (IncidentType.ROADWORKS, IncidentSeverity.MINOR, "Resurfacing between Kafue and Chilanga; one lane open", True),
        (IncidentType.CONGESTION, IncidentSeverity.MINOR, "Heavy traffic into town at the Kafue Road roundabout", True),
        (IncidentType.ACCIDENT, IncidentSeverity.SERIOUS, "Minibus and truck collided; ambulance on scene", True),
        (IncidentType.MAINTENANCE, IncidentSeverity.MINOR, "Streetlights being repaired", True),
        (IncidentType.ROAD_CLOSED, IncidentSeverity.SERIOUS, "Flooded section after heavy rain; use the bypass", True),
        (IncidentType.ACCIDENT, IncidentSeverity.MINOR, "Fender bender cleared by police", False),
        (IncidentType.ROADWORKS, IncidentSeverity.MINOR, "Pothole repairs finished", False),
    ]
    for t, s, text, active in items:
        r = rng.choice(roads)
        start = when(20, 0)
        db.add(RoadIncident(incident_type=t, severity=s, road_id=r.id, description="[demo] " + text, starts_at=start,
                            ends_at=None if active else start + timedelta(hours=rng.randint(2, 30)), is_active=active,
                            verification="official"))
    return len(items)


def make_licence_applications(db, citizens):
    statuses = [LicenceApplicationStatus.SUBMITTED, LicenceApplicationStatus.THEORY_TEST_SCHEDULED, LicenceApplicationStatus.THEORY_TEST_PASSED,
                LicenceApplicationStatus.THEORY_TEST_FAILED, LicenceApplicationStatus.PRACTICAL_TEST_SCHEDULED,
                LicenceApplicationStatus.PRACTICAL_TEST_PASSED, LicenceApplicationStatus.ISSUED, LicenceApplicationStatus.REJECTED]
    for i, u in enumerate(citizens[-8:]):
        first, _, last = u.full_name.partition(" ")
        st = statuses[i % len(statuses)]
        app = LicenceApplication(applicant_id=u.id, first_name=first, last_name=last, id_number=u.nrc_number.replace("/", ""),
                                 date_of_birth=NOW - timedelta(days=365 * rng.randint(19, 40)), requested_class=LicenceClass.B, status=st)
        if st in (LicenceApplicationStatus.THEORY_TEST_PASSED, LicenceApplicationStatus.PRACTICAL_TEST_SCHEDULED,
                  LicenceApplicationStatus.PRACTICAL_TEST_PASSED, LicenceApplicationStatus.ISSUED):
            app.theory_score = rng.randint(72, 96)
        if st == LicenceApplicationStatus.THEORY_TEST_FAILED:
            app.theory_score = rng.randint(30, 58)
        if st in (LicenceApplicationStatus.PRACTICAL_TEST_PASSED, LicenceApplicationStatus.ISSUED):
            app.practical_score = rng.randint(70, 95)
        app.created_at = when(90, 2)
        db.add(app)


def make_extras(db, vehicles, drivers, officers, citizens):
    """The modules the fines, tolls and accidents above do not touch: policy types, test checklists,
    service fees, ANPR sightings, driver reports, PSV route alerts, agency integrations, reconciliation."""
    import json

    from app.core.security import hash_password as _h  # noqa: F401
    from app.services import inspection_rules as rules
    from app.services.integration import generate_key
    from app.services.payments import create_payment as _pay

    vids = {v.id: v for v in vehicles}
    permit_vehicles = {p.vehicle_id for p in db.query(PSVPermit).filter(PSVPermit.vehicle_id.in_(list(vids))).all()}
    # registration: most vehicles are paid up for months, a few fall due inside the renewal window
    for i, v in enumerate(vehicles):
        v.registration_expiry = datetime.utcnow() + timedelta(days=(rng.randint(10, 55) if i % 5 == 0 else rng.randint(90, 330)))
    # licence applications: leave the fee unpaid on in-progress ones so "Pay licence application fee" has something to pay
    for a in db.query(LicenceApplication).all():
        if a.status.value == "issued":
            a.fee_paid_at = a.created_at
        elif a.status.value in ("submitted", "theory_test_scheduled") and a.id.int % 2:
            a.fee_paid_at = None
        else:
            a.fee_paid_at = a.created_at
    # 1. policy types and premiums
    typed = 0
    for pol in db.query(Insurance).filter(Insurance.vehicle_id.in_(list(vids))).all():
        v = vids[pol.vehicle_id]
        if v.id in permit_vehicles:
            pol.policy_type, pol.premium = "psv_passenger", rng.randint(280000, 520000)
        elif v.make in ("Scania", "FAW") or v.model in ("Canter", "Truck"):
            pol.policy_type, pol.premium = "goods_in_transit", rng.randint(450000, 900000)
        else:
            pol.policy_type = rng.choices(["third_party", "third_party_fire_theft", "comprehensive", "comesa_yellow_card"], weights=[50, 20, 25, 5])[0]
            pol.premium = {"third_party": rng.randint(90000, 160000), "third_party_fire_theft": rng.randint(180000, 320000),
                           "comprehensive": rng.randint(350000, 900000), "comesa_yellow_card": rng.randint(60000, 120000)}[pol.policy_type]
        typed += 1
    # every permitted vehicle has live passenger-liability cover, so renewal works in the demo
    for vid in permit_vehicles:
        if not db.query(Insurance).filter(Insurance.vehicle_id == vid, Insurance.policy_type == "psv_passenger", Insurance.is_active == True,  # noqa: E712
                                          Insurance.end_date >= NOW).first():
            db.query(Insurance).filter(Insurance.vehicle_id == vid, Insurance.is_active == True).update({"is_active": False})  # noqa: E712
            db.add(Insurance(vehicle_id=vid, provider=rng.choice(INSURERS), policy_number=f"POL-{rng.randint(10**6, 10**7 - 1)}",
                             policy_type="psv_passenger", premium=rng.randint(280000, 520000), start_date=NOW - timedelta(days=120),
                             end_date=NOW + timedelta(days=245), is_active=True))
    # 2. checklists and readings on past inspections
    items = [c[0] for c in rules.CHECKLIST]
    for ins in db.query(Inspection).filter(Inspection.vehicle_id.in_(list(vids))).all():
        if ins.result == InspectionResult.PASSED:
            ins.checklist = rules.dump({k: "pass" for k in items})
            ins.readings = rules.dump({"brake_efficiency_pct": rng.randint(58, 82), "tyre_tread_mm": round(rng.uniform(2.2, 7.5), 1),
                                       "emission_co_pct": round(rng.uniform(0.3, 3.2), 1)})
            if rng.random() < 0.25:
                ins.obd_codes = rules.dump([rng.choice(["P0128", "P0442", "P0171"])])  # advisory only
        elif ins.result == InspectionResult.FAILED:
            bad = rng.choice(["brakes", "tyres", "lights", "exhaust"])
            ins.checklist = rules.dump({k: ("fail" if k == bad else "pass") for k in items})
            if bad == "exhaust":
                ins.obd_codes = rules.dump(["P0420"])
            ins.readings = rules.dump({"brake_efficiency_pct": rng.randint(30, 49) if bad == "brakes" else rng.randint(55, 70),
                                       "tyre_tread_mm": round(rng.uniform(0.6, 1.4), 1) if bad == "tyres" else round(rng.uniform(2.5, 6), 1)})
    # 3. fees paid through the services screen: booked tests, permit renewals, replacement cards
    owners = {u.id: u for u in citizens}
    booked = paid_fees = 0
    for v in vehicles:
        u = owners.get(v.user_id)
        if u is None or booked >= 6:
            continue
        if db.query(Inspection).filter(Inspection.vehicle_id == v.id, Inspection.result == InspectionResult.PENDING).first():
            continue
        db.add(Inspection(vehicle_id=v.id, inspection_centre=rng.choice(CENTRES), scheduled_date=NOW + timedelta(days=rng.randint(1, 12))))
        _pay(db, u, PaymentType.FEE, v.id, 15000, description=f"Fitness test fee {v.registration_number}")
        booked += 1
        paid_fees += 1
    for pid in list(permit_vehicles)[:3]:
        v = vids[pid]
        u = owners.get(v.user_id)
        permit = db.query(PSVPermit).filter(PSVPermit.vehicle_id == pid).first()
        if u and permit:
            _pay(db, u, PaymentType.PERMIT, permit.id, 120000, description=f"PSV permit renewal {permit.permit_number}")
            paid_fees += 1
    for d in [x for x in drivers if x.user_id in owners][:3]:
        _pay(db, owners[d.user_id], PaymentType.FEE, d.id, 10000, description=f"Replacement licence card {d.licence_number}")
        paid_fees += 1
    # 4. ANPR sightings
    cameras = [("DEMO-CAM-01", "Great East Road, Lusaka"), ("DEMO-CAM-02", "Cairo Road, Lusaka"), ("DEMO-CAM-03", "Kafue Road, Chilanga"),
               ("DEMO-CAM-04", "Ndola-Kitwe Dual Carriageway")]
    for i in range(46):
        cam = rng.choice(cameras)
        if i % 9 == 0:
            plate, vid = f"{rng.choice(PLATE_LETTERS)} {rng.randint(1000, 9999)}", None  # a plate nobody has registered
        else:
            v = rng.choice(vehicles); plate, vid = v.registration_number, v.id
        db.add(ANPREvent(plate_number=plate, vehicle_id=vid, location=cam[1], camera_id=cam[0],
                         timestamp=NOW - timedelta(minutes=rng.randint(5, 60 * 24 * 30)), confidence=round(rng.uniform(0.74, 0.99), 2)))
    stolen = next((x for x in vehicles if x.status == VehicleStatus.STOLEN and x.user_id == citizens[3].id), None)
    if stolen is not None:
        db.add(ANPREvent(plate_number=stolen.registration_number, vehicle_id=stolen.id, location="Great East Road, Lusaka", camera_id="DEMO-CAM-01",
                         timestamp=NOW - timedelta(hours=3), confidence=0.97))
    # 5. citizen reports of other drivers
    types = ["using_phone", "speeding", "running_red_light", "reckless_driving", "rear_seat_belt"]
    statuses = ["pending", "pending", "pending", "accepted", "accepted", "rejected", "false", "pending"]
    for i, st in enumerate(statuses):
        rep = citizens[(i + 4) % len(citizens)]
        v = rng.choice([x for x in vehicles if x.user_id != rep.id])
        db.add(DriverReport(reporter_id=rep.id, plate_number=v.registration_number, vehicle_id=v.id, violation_type=rng.choice(types),
                            location=(loc := rng.choice(LOCATIONS))[0], latitude=loc[1], longitude=loc[2], observed_at=NOW - timedelta(hours=rng.randint(2, 200)),
                            description=rng.choice(["Driver was on a phone call with one hand on the wheel at the lights.",
                                                    "Overtook on a solid line near the roundabout and cut in front of a bus.",
                                                    "Went through the red light after it had been red for several seconds.",
                                                    "Weaving between lanes at high speed, nearly hit a minibus."]),
                            status=st, reviewed_by=None if st == "pending" else officers[0].id,
                            reviewed_at=None if st == "pending" else NOW - timedelta(hours=rng.randint(1, 40)),
                            review_note=None if st == "pending" else {"accepted": "Matches the camera sighting. Fine issued.",
                                                                       "rejected": "Evidence too unclear to act on.", "false": "Plate was elsewhere at that time."}[st]))
    # 6. PSV vehicles that left their route corridor
    alerts = 0
    for p in db.query(PSVPermit).filter(PSVPermit.vehicle_id.in_(list(vids))).all():
        if db.query(PSVCorridor).filter(PSVCorridor.permit_id == p.id).first() and alerts < 7:
            dist = rng.randint(900, 6000)
            db.add(PSVRouteAlert(permit_id=p.id, vehicle_id=p.vehicle_id, latitude=-15.35 + rng.random() * 0.1, longitude=28.3 + rng.random() * 0.3,
                                 distance_m=dist, buffer_m=500, occurred_at=NOW - timedelta(hours=rng.randint(1, 160)),
                                 acknowledged=rng.random() < 0.4, acknowledged_by=officers[0].id if alerts % 3 == 0 else None))
            alerts += 1
    # 7. partner agencies and what they have called
    agencies = [("[demo] Zambia Police Service", "police", "vehicles:read,drivers:read,accidents:write", "traffic@police.example.zm"),
                ("[demo] Insurers Association", "insurance", "insurance:read,insurance:write", "api@insurers.example.zm"),
                ("[demo] Toll Authority", "toll_authority", "compliance:read,vehicles:read", "ops@toll.example.zm")]
    clients = []
    for name, typ, scopes, mail in agencies:
        full, prefix, digest = generate_key()
        c = AgencyClient(name=name, agency_type=typ, api_key_prefix=prefix, api_key_hash=digest, scopes=scopes, contact_email=mail,
                         data_sharing_agreement="Demo agreement: read access for enforcement and compliance checks.",
                         contract_expires_at=NOW + timedelta(days=rng.randint(60, 700)), last_used_at=NOW - timedelta(minutes=rng.randint(2, 600)))
        db.add(c); clients.append(c)
    db.flush()
    for i in range(70):
        c = rng.choice(clients)
        ok = rng.random() > 0.08
        ep = {"police": "/api/integration/vehicles/lookup", "insurance": "/api/integration/insurance", "toll_authority": "/api/integration/compliance"}[c.agency_type]
        db.add(IntegrationLog(agency_id=c.id, agency_name=c.name, direction="inbound", endpoint=ep, status_code=200 if ok else rng.choice([403, 404, 429]),
                              success=ok, latency_ms=round(rng.uniform(18, 240), 1), detail=None if ok else "Rejected: outside the agreed scope or rate limit",
                              created_at=NOW - timedelta(minutes=rng.randint(1, 60 * 24 * 14))))
    # 8. one gateway reconciliation
    ledger = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == PaymentStatus.COMPLETED).scalar() or 0
    done = db.query(func.count()).select_from(Payment).filter(Payment.status == PaymentStatus.COMPLETED).scalar() or 0
    db.add(ReconciliationRun(gateway="mobile_money", statement_total=int(ledger), ledger_total=int(ledger), matched=done, mismatched=0,
                             missing_in_ledger=0, missing_in_statement=0, report='{"note": "Demo run: statement and ledger agree.", "mismatches": []}', run_by=officers[0].id))
    db.flush()
    return {"policies typed": typed, "service fees paid": paid_fees, "tests booked": booked, "alerts": alerts}


def summary(db) -> None:
    from sqlalchemy import func
    paid = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == PaymentStatus.COMPLETED,
                                                                        Payment.payment_type == PaymentType.FINE).scalar()
    out = db.query(func.coalesce(func.sum(Challan.penalty_amount), 0)).filter(
        Challan.status.in_([ChallanStatus.UNPAID, ChallanStatus.OVERDUE, ChallanStatus.DISPUTED])).scalar()
    print(f"  fines collected: {zmw(paid)}   outstanding: {zmw(out)}")
    for label, model in [("users", User), ("vehicles", Vehicle), ("drivers", Driver), ("violations", Violation), ("e-Challans", Challan),
                         ("payments", Payment), ("toll transactions", TollTransaction), ("PSV permits", PSVPermit),
                         ("accidents", Accident), ("licence applications", LicenceApplication)]:
        print(f"  {label}: {db.query(model).count()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="remove earlier demo data first")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        if args.reset:
            reset(db)
        if db.query(User).filter(User.email.like(f"%@{CITIZEN_DOMAIN}")).first():
            print("Demo data is already there. Run with --reset to rebuild it.")
            return 0
        if not db.query(Road).first():
            print("Run  python scripts/seed.py  first (it creates the road network and notification rules).")
            return 1
        staff, citizens = make_users(db)
        officers = [u for u in staff if u.role == UserRole.OFFICER]
        toll_ops = [u for u in staff if u.role == UserRole.TOLL_OPERATOR]
        drivers, vehicles = make_drivers_and_vehicles(db, citizens)
        make_documents(db, vehicles, officers)
        fine_stats = make_fines(db, vehicles, drivers, officers, citizens)
        tolls = make_tolls(db, vehicles, toll_ops, citizens)
        psv = make_psv(db, vehicles, officers)
        acc = make_accidents(db, vehicles, drivers, officers)
        inc = make_incidents(db)
        make_licence_applications(db, citizens)
        nom = make_nomination_demo(db, vehicles, drivers, officers, citizens)
        print(f"Driver-nomination demo: {nom}")
        print(f"Other modules: {make_extras(db, vehicles, drivers, officers, citizens)}")
        from app.services.overdue import mark_overdue_challans
        overdue = mark_overdue_challans(db)
        db.commit()
        print(f"Overdue pass: {overdue}")
        print(f"Demo data added: fines {fine_stats}, tolls {tolls}, PSV permits {psv}, accidents {acc}, road incidents {inc}")
        summary(db)
        print(f"Sign in as any demo account with password {PASSWORD}, for example "
              f"admin.finance@{STAFF_DOMAIN}, officer.chanda@{STAFF_DOMAIN}, toll.kafue@{STAFF_DOMAIN}, or {citizens[0].email}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
