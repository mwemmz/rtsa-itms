"""Location fields must not accept blank or oversized values.

A bare `str` in pydantic v2 accepts "", so these schemas silently allowed
records with no place. The upper bound matters just as much: the database
columns are VARCHAR(200), so anything longer passes validation and then fails
at the insert with a 500 instead of a 422.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TRUST_PROXY_HEADERS"] = "true"

import uuid  # noqa: E402

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.models.accident import AccidentSeverity  # noqa: E402
from app.schemas.accident import AccidentCreate  # noqa: E402
from app.schemas.anpr import ANPREventCreate  # noqa: E402
from app.schemas.enforcement import ViolationCreate  # noqa: E402
from app.models.enforcement import ViolationType  # noqa: E402

MAX = 200


def _violation(location: str) -> ViolationCreate:
    # vehicle_id is only here to satisfy ViolationCreate's "needs an offender"
    # rule -- these tests are about the location field, not who's charged.
    return ViolationCreate(
        violation_type=ViolationType.SPEEDING, location=location, vehicle_id=uuid.uuid4()
    )


def _accident(location: str) -> AccidentCreate:
    return AccidentCreate(
        location=location,
        occurred_at="2026-01-01T00:00:00",
        severity=AccidentSeverity.MINOR,
    )


def _anpr(location: str) -> ANPREventCreate:
    return ANPREventCreate(plate_number="RA 123 ABC", location=location)


BUILDERS = {
    "violation": _violation,
    "accident": _accident,
    "anpr": _anpr,
}


@pytest.mark.parametrize("build", BUILDERS.values(), ids=list(BUILDERS))
@pytest.mark.parametrize("location", ["", " ", "\t\n"])
def test_rejects_blank_location(build, location):
    with pytest.raises(ValidationError):
        build(location)


@pytest.mark.parametrize("build", BUILDERS.values(), ids=list(BUILDERS))
def test_rejects_location_longer_than_column(build):
    with pytest.raises(ValidationError):
        build("x" * (MAX + 1))


@pytest.mark.parametrize("build", BUILDERS.values(), ids=list(BUILDERS))
def test_accepts_location_at_column_limit(build):
    assert len(build("x" * MAX).location) == MAX


@pytest.mark.parametrize("build", BUILDERS.values(), ids=list(BUILDERS))
def test_trims_surrounding_whitespace(build):
    assert build("  Mzee Nyerere Road, Dar es Salaam  ").location == (
        "Mzee Nyerere Road, Dar es Salaam"
    )
