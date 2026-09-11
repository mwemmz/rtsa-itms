"""Seed default notification rules and demo data.

Usage:
    python scripts/seed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.models.notification import NotificationRule


DEFAULT_RULES = [
    {
        "trigger_event": "challan_created",
        "channels": "in_app,email",
        "title_template": "e-Challan issued",
        "body_template": "You have been issued e-Challan {reference} for {amount} due by {due_date}.",
    },
    {
        "trigger_event": "licence_expiring",
        "channels": "in_app,sms",
        "title_template": "Licence expiring soon",
        "body_template": "Your licence {licence_number} expires on {expiry_date}. Please renew.",
    },
    {
        "trigger_event": "insurance_expiring",
        "channels": "in_app,email",
        "title_template": "Insurance expiring soon",
        "body_template": "Insurance for vehicle {registration} expires on {expiry_date}.",
    },
    {
        "trigger_event": "fitness_expiring",
        "channels": "in_app,sms",
        "title_template": "Fitness certificate expiring",
        "body_template": "Fitness certificate for {registration} expires on {expiry_date}.",
    },
    {
        "trigger_event": "toll_flagged",
        "channels": "in_app,email",
        "title_template": "Toll compliance alert",
        "body_template": "Vehicle {registration} was flagged at toll gate {gate_id}: {issues}.",
    },
    {
        "trigger_event": "payment_receipt",
        "channels": "in_app,email",
        "title_template": "Payment receipt",
        "body_template": "Your payment of {amount} with reference {reference} was successful.",
    },
    {
        "trigger_event": "road_alert",
        "channels": "in_app,sms",
        "title_template": "Road alert: {incident_type}",
        "body_template": "{incident_type} reported on {road}. {description} Suggested: {suggestion}",
    },
    {
        "trigger_event": "licence_renewed",
        "channels": "in_app,email",
        "title_template": "Licence renewed",
        "body_template": "Your licence {licence_number} has been renewed. New expiry: {expiry_date}.",
    },
]


def seed_notification_rules() -> None:
    db = SessionLocal()
    try:
        existing = {r.trigger_event for r in db.query(NotificationRule).all()}
        added = 0
        for rule_data in DEFAULT_RULES:
            if rule_data["trigger_event"] not in existing:
                db.add(NotificationRule(**rule_data))
                added += 1
        db.commit()
        print(f"Notification rules: {added} added, {len(DEFAULT_RULES) - added} already present")
    finally:
        db.close()


def seed_demo_users() -> None:
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == "admin@rtsa.gov.zm").first():
            print("Demo users already present")
            return
        demo_users = [
            User(
                email="admin@rtsa.gov.zm",
                hashed_password=hash_password("admin123"),
                full_name="System Administrator",
                role=UserRole.ADMIN,
            ),
            User(
                email="officer@rtsa.gov.zm",
                hashed_password=hash_password("officer123"),
                full_name="Traffic Officer",
                role=UserRole.OFFICER,
            ),
            User(
                email="citizen@example.com",
                hashed_password=hash_password("citizen123"),
                full_name="John Mwale",
                role=UserRole.CITIZEN,
            ),
        ]
        db.add_all(demo_users)
        db.commit()
        print("Demo users created: admin@rtsa.gov.zm / admin123, officer@rtsa.gov.zm / officer123, citizen@example.com / citizen123")
    finally:
        db.close()


def seed_demo_vehicles() -> None:
    from datetime import datetime

    from app.models.inspection import FitnessCertificate, Inspection, InspectionResult
    from app.models.insurance import Insurance
    from app.models.user import User
    from app.models.vehicle import Vehicle

    db = SessionLocal()
    try:
        if db.query(Vehicle).first():
            print("Demo vehicles already present")
            return
        admin = db.query(User).filter(User.email == "admin@rtsa.gov.zm").first()

        # Compliant vehicle
        compliant = Vehicle(
            registration_number="BAL 1234",
            owner_name="John Mwale",
            owner_id_number="123456",
            make="Toyota",
            model="Hilux",
            year=2022,
            color="White",
        )
        # Non-compliant vehicle (no insurance, no fitness)
        non_compliant = Vehicle(
            registration_number="BAL 5678",
            owner_name="John Mwale",
            owner_id_number="123456",
            make="Nissan",
            model="Navara",
            year=2015,
            color="Black",
        )
        # Blacklisted vehicle
        blacklisted = Vehicle(
            registration_number="BAL 9999",
            owner_name="Jane Banda",
            owner_id_number="654321",
            make="Mazda",
            model="BT-50",
            year=2010,
            color="Red",
            is_blacklisted=True,
            blacklist_reason="Suspected stolen vehicle",
        )
        db.add_all([compliant, non_compliant, blacklisted])
        db.flush()

        # Give compliant vehicle insurance + fitness
        db.add(
            Insurance(
                vehicle_id=compliant.id,
                provider="Sanlam Zambia",
                policy_number="POL-COMPLIANT-001",
                start_date=datetime.utcnow(),
                end_date=datetime(2030, 12, 31),
            )
        )
        inspection = Inspection(
            vehicle_id=compliant.id,
            inspection_centre="Lusaka Road Test Centre",
            scheduled_date=datetime.utcnow(),
            result=InspectionResult.PASSED,
        )
        db.add(inspection)
        db.flush()
        db.add(
            FitnessCertificate(
                inspection_id=inspection.id,
                vehicle_id=compliant.id,
                certificate_number="FIT-COMPLIANT-001",
                issued_date=datetime.utcnow(),
                expiry_date=datetime(2030, 12, 31),
            )
        )

        db.commit()
        print("Demo vehicles created: BAL 1234 (compliant), BAL 5678 (non-compliant), BAL 9999 (blacklisted)")
    finally:
        db.close()


def seed_road_network() -> None:
    """Seed the Lusaka road network so the planner / leaflet / alerts work."""
    from datetime import datetime, timedelta

    from app.models.road_network import (
        IncidentSeverity,
        IncidentType,
        Intersection,
        Road,
        RoadClass,
        RoadIncident,
        RoadSegment,
    )

    db = SessionLocal()
    try:
        if db.query(Road).first():
            print("Road network already present")
            return

        intersections = [
            Intersection(name="Great East / Airport Junction", latitude=-15.4063, longitude=28.3990),
            Intersection(name="Great East / Lumumba Junction", latitude=-15.3933, longitude=28.3304),
            Intersection(name="Cross Roads (Great East / Great North)", latitude=-15.3830, longitude=28.3450),
            Intersection(name="Thabo Mbeki Interchange", latitude=-15.3897, longitude=28.2981),
            Intersection(name="CBD - Cairo Road", latitude=-15.4155, longitude=28.2818),
            Intersection(name="Chudleigh / Thabo Mbeki", latitude=-15.3710, longitude=28.3150),
            Intersection(name="Independence Avenue - CBD", latitude=-15.4181, longitude=28.2770),
            Intersection(name="Addis Ababa / Independence", latitude=-15.4020, longitude=28.3300),
            Intersection(name="Los Angeles / Independence", latitude=-15.4210, longitude=28.2620),
            Intersection(name="Kafue Road Junction", latitude=-15.4520, longitude=28.2630),
            Intersection(name="Great North Road Junction", latitude=-15.3470, longitude=28.2880),
            Intersection(name="Mumbwa Road West", latitude=-15.4010, longitude=28.1900),
        ]
        db.add_all(intersections)
        db.flush()
        ix = {i.name: i for i in intersections}

        roads = [
            Road(name="Great East Road", road_class=RoadClass.HIGHWAY),
            Road(name="Cairo Road", road_class=RoadClass.MAIN_ROAD),
            Road(name="Independence Avenue", road_class=RoadClass.MAIN_ROAD),
            Road(name="Addis Ababa Drive", road_class=RoadClass.MAIN_ROAD),
            Road(name="Great North Road", road_class=RoadClass.MAIN_ROAD),
            Road(name="Mumbwa Road", road_class=RoadClass.MAIN_ROAD),
            Road(name="Kafue Road", road_class=RoadClass.MAIN_ROAD),
            Road(name="Thabo Mbeki Road", road_class=RoadClass.MAIN_ROAD),
            Road(name="Los Angeles Boulevard", road_class=RoadClass.MAIN_ROAD),
        ]
        db.add_all(roads)
        db.flush()
        r = {road.name: road for road in roads}

        # (road, start, end, distance_km, travel_minutes)
        segments = [
            ("Great East Road", "Great East / Airport Junction", "Great East / Lumumba Junction", 7.5, 10.0),
            ("Great East Road", "Great East / Lumumba Junction", "Cross Roads (Great East / Great North)", 2.2, 3.0),
            ("Great East Road", "Cross Roads (Great East / Great North)", "Thabo Mbeki Interchange", 3.5, 5.0),
            ("Great East Road", "Thabo Mbeki Interchange", "CBD - Cairo Road", 3.0, 4.0),
            ("Great East Road", "Cross Roads (Great East / Great North)", "Chudleigh / Thabo Mbeki", 4.0, 6.0),
            ("Cairo Road", "CBD - Cairo Road", "Independence Avenue - CBD", 0.8, 2.0),
            ("Independence Avenue", "Independence Avenue - CBD", "Addis Ababa / Independence", 3.5, 5.0),
            ("Addis Ababa Drive", "Addis Ababa / Independence", "Cross Roads (Great East / Great North)", 3.2, 5.0),
            ("Independence Avenue", "Independence Avenue - CBD", "Los Angeles / Independence", 3.0, 4.0),
            ("Los Angeles Boulevard", "Los Angeles / Independence", "Kafue Road Junction", 2.4, 3.0),
            ("Kafue Road", "Kafue Road Junction", "CBD - Cairo Road", 6.0, 8.0),
            ("Great North Road", "Great North Road Junction", "Cross Roads (Great East / Great North)", 3.0, 4.0),
            ("Great North Road", "Great North Road Junction", "Mumbwa Road West", 5.0, 7.0),
            ("Mumbwa Road", "Mumbwa Road West", "Independence Avenue - CBD", 7.0, 9.0),
            ("Thabo Mbeki Road", "Thabo Mbeki Interchange", "Chudleigh / Thabo Mbeki", 2.0, 3.0),
            ("Thabo Mbeki Road", "Chudleigh / Thabo Mbeki", "Great North Road Junction", 2.6, 4.0),
        ]
        for road_name, start_name, end_name, dist, minutes in segments:
            db.add(
                RoadSegment(
                    road_id=r[road_name].id,
                    start_intersection_id=ix[start_name].id,
                    end_intersection_id=ix[end_name].id,
                    distance_km=dist,
                    travel_minutes=minutes,
                )
            )
        db.flush()

        # Live demo incident: minor accident on Great East between Cross Roads and Thabo Mbeki,
        # so the planner immediately demonstrates rerouting.
        ge3 = (
            db.query(RoadSegment)
            .join(Road, RoadSegment.road_id == Road.id)
            .filter(
                Road.name == "Great East Road",
                RoadSegment.start_intersection_id
                == ix["Cross Roads (Great East / Great North)"].id,
                RoadSegment.end_intersection_id == ix["Thabo Mbeki Interchange"].id,
            )
            .first()
        )
        if ge3:
            db.add(
                RoadIncident(
                    incident_type=IncidentType.ACCIDENT,
                    severity=IncidentSeverity.MINOR,
                    segment_id=ge3.id,
                    road_id=r["Great East Road"].id,
                    description="Minor accident blocking the inner lane.",
                    starts_at=datetime.utcnow() - timedelta(minutes=45),
                )
            )

        db.commit()
        print(
            f"Lusaka network seeded: {len(intersections)} intersections, "
            f"{len(roads)} roads, {len(segments)} segments + 1 live incident"
        )
    finally:
        db.close()


def seed_demo_portal() -> None:
    """Link the citizen demo user to a licence + vehicles and add an unpaid fine."""
    from datetime import datetime, timedelta

    from app.models.driver import Driver, LicenceClass
    from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
    from app.models.insurance import Insurance
    from app.models.user import User
    from app.models.vehicle import Vehicle

    db = SessionLocal()
    try:
        citizen = db.query(User).filter(User.email == "citizen@example.com").first()
        if not citizen:
            print("Citizen user not found; skipping portal demo")
            return
        if db.query(Driver).filter(Driver.user_id == citizen.id).first():
            print("Portal demo already present")
            return

        # Connect existing demo vehicles to John Mwale's account
        bal1234 = db.query(Vehicle).filter(Vehicle.registration_number == "BAL 1234").first()
        bal5678 = db.query(Vehicle).filter(Vehicle.registration_number == "BAL 5678").first()
        if bal1234:
            bal1234.user_id = citizen.id
        if bal5678:
            bal5678.user_id = citizen.id

        # Driving licence expiring in 25 days -> portal shows "expiring soon"
        db.add(
            Driver(
                user_id=citizen.id,
                licence_number="ZM-CITIZEN-2021",
                first_name="John",
                last_name="Mwale",
                id_number="123456",
                date_of_birth=datetime(1985, 4, 12),
                phone_number="+260977000000",
                email="citizen@example.com",
                licence_class=LicenceClass.B,
                licence_issue_date=datetime.utcnow() - timedelta(days=300),
                licence_expiry_date=datetime.utcnow() + timedelta(days=25),
            )
        )

        # Outstanding fine for the non-compliant vehicle
        if bal5678:
            violation = Violation(
                vehicle_id=bal5678.id,
                violation_type=ViolationType.SPEEDING,
                location="Great East Road - Lumumba Checkpoint",
                timestamp=datetime.utcnow() - timedelta(days=6),
                description="Travelling at 98 km/h in an 80 km/h zone.",
            )
            db.add(violation)
            db.flush()
            db.add(
                Challan(
                    reference="CH-2024-0009",
                    violation_id=violation.id,
                    vehicle_id=bal5678.id,
                    penalty_amount=600,
                    due_date=datetime.utcnow() + timedelta(days=14),
                    status=ChallanStatus.UNPAID,
                )
            )

        if bal1234:
            db.add(
                Insurance(
                    vehicle_id=bal1234.id,
                    provider="Sanlam Zambia",
                    policy_number="POL-COMPLIANT-002",
                    start_date=datetime.utcnow() - timedelta(days=30),
                    end_date=datetime.utcnow() + timedelta(days=335),
                )
            )

        db.commit()
        print("Portal demo created: John Mwale licence expires in 25 days; BAL 5678 has an unpaid ZMW 600 fine")
    finally:
        db.close()


if __name__ == "__main__":
    seed_notification_rules()
    seed_demo_users()
    seed_demo_vehicles()
    seed_road_network()
    seed_demo_portal()
    print("Seed complete.")