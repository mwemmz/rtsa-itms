import uuid as uuid_lib

from sqlalchemy import Uuid
from sqlalchemy.types import TypeDecorator


class UUIDType(TypeDecorator):
    """A UUID column type that accepts both uuid.UUID and string values.

    Useful so path parameters (always strings in FastAPI) can be bound
    directly against UUID columns across Postgres and SQLite.
    """

    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and not isinstance(value, uuid_lib.UUID):
            return uuid_lib.UUID(str(value))
        return value

    def process_result_value(self, value, dialect):
        return value