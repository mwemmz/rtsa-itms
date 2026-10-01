"""Shared field types for request schemas.

These exist because pydantic v2's bare `str` accepts "" and whitespace-only
text, and because it applies no length bound. Both let bad records reach the
database, where a value longer than the column raises a 500 on insert instead of
a 422 at validation time.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, StringConstraints

# Every `location` column in the schema is VARCHAR(200). The bound is repeated
# here so a wider column elsewhere cannot silently drift past validation.
LOCATION_MAX_LENGTH = 200

# strip_whitespace runs before the length checks, so "   " is rejected as blank
# rather than counted as three characters of location.
LocationStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=LOCATION_MAX_LENGTH
    ),
]


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# The app stores naive UTC (datetime.utcnow()) in timestamp-without-time-zone
# columns. An offset-aware value from the browser ("...Z") would otherwise be
# converted with the database session's time zone, so a server set to local time
# saved accidents hours "in the future" and reports filtering on utcnow() skipped
# them.
UtcDatetime = Annotated[datetime, AfterValidator(_to_naive_utc)]
