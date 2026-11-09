"""Input validation: bad values are refused with a clear message, never a server error."""
import pytest
from pydantic import ValidationError

from app.schemas import validators as V
from app.schemas.driver import DriverCreate
from app.schemas.insurance import InsuranceCreate
from app.schemas.psv import PSVOperatorCreate, PSVPermitCreate
from app.schemas.toll import TollEventCreate
from app.schemas.user import AdminUserCreate
from app.schemas.vehicle import VehicleCreate
import uuid


def test_validators():
    assert V.phone("+260 971 234567") == "+260 971 234567"
    assert V.phone("") is None
    for bad in ("abc", "12345", "0971"):
        with pytest.raises(ValueError):
            V.phone(bad)
    assert V.email(" A@B.co ") == "a@b.co"
    with pytest.raises(ValueError):
        V.email("not-an-email")
    with pytest.raises(ValueError):
        V.person_name("123")
    with pytest.raises(ValueError):
        V.clean("<script>")


def test_vehicle_and_driver_reject_junk():
    ok = dict(registration_number="BAK 123", owner_name="Own Er", owner_id_number="123456/78/1", make="Hino", model="300", year=2020)
    assert VehicleCreate(**ok).model == "300"
    for field, value in (("owner_id_number", "abc"), ("owner_name", "12345"), ("color", "<b>x</b>")):
        with pytest.raises(ValidationError):
            VehicleCreate(**dict(ok, **{field: value}))
    d = dict(licence_number="DL-1234", first_name="A", last_name="B", id_number="123456/78/1", date_of_birth="1990-01-01T00:00:00",
             licence_class="B", licence_issue_date="2024-01-01T00:00:00", licence_expiry_date="2030-01-01T00:00:00")
    DriverCreate(**d)
    for field, value in (("first_name", "123"), ("phone_number", "abc"), ("email", "bad")):
        with pytest.raises(ValidationError):
            DriverCreate(**dict(d, **{field: value}))


def test_toll_psv_and_admin_user():
    with pytest.raises(ValidationError):
        TollEventCreate(plate_number="BAK 123", gate_id="G1", toll_amount=-5)
    with pytest.raises(ValidationError):
        TollEventCreate(plate_number="BAK 123", gate_id="")
    with pytest.raises(ValidationError):
        TollEventCreate(plate_number="BAK 123", gate_id="G1", timestamp="2099-01-01T00:00:00")
    with pytest.raises(ValidationError):
        PSVOperatorCreate(name="O", licence_number="L1", contact_person="C", phone="abc")
    with pytest.raises(ValidationError):
        AdminUserCreate(email="bad", password="Demo12345!x", full_name="W")
    with pytest.raises(ValidationError):
        AdminUserCreate(email="a@b.co", password="Demo12345!x", full_name="")


def test_timezone_aware_dates_do_not_crash():
    for z in ("2026-06-01T10:00:00Z", "2026-06-01T10:00:00+02:00"):
        InsuranceCreate(vehicle_id=uuid.uuid4(), provider="X", policy_number="P", start_date=z, end_date="2027-06-01T10:00:00")
        PSVPermitCreate(operator_id=uuid.uuid4(), vehicle_id=uuid.uuid4(), permit_number="P", route="A-B", issued_date=z, expiry_date="2027-06-01T10:00:00")
