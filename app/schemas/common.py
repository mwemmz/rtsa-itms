"""Shared response envelopes, pagination and error types."""

from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationMeta(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False
    limit: int = 50


class PagedResponse(BaseModel, Generic[T]):
    data: list[T]
    pagination: PaginationMeta


class ErrorDetail(BaseModel):
    field: str | None = None
    issue: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None
    timestamp: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorEnvelope


class MessageResponse(BaseModel):
    message: str


class StatusResponse(BaseModel):
    status: str
