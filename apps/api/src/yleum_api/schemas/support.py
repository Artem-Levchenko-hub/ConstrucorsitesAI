from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

SupportStatus = Literal["new", "in_progress", "done"]


class CreateTicket(BaseModel):
    request_id: UUID
    subject: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=8000)
    email: EmailStr | None = None
    privacy_accepted: bool = False
    guest_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_path: str | None = Field(default=None, max_length=512)
    website: str = Field(default="", max_length=200)

    @field_validator("subject", "message", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_path")
    @classmethod
    def relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("source_path must be a relative path")
        return urlsplit(value).path


class CreateMessage(BaseModel):
    request_id: UUID
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ChangeStatus(BaseModel):
    status: SupportStatus
    expected_version: int = Field(ge=1)


class SupportContext(BaseModel):
    user_id: UUID | None
    email: str | None
    is_admin: bool


class Message(BaseModel):
    id: UUID
    author: Literal["customer", "support"]
    body: str
    created_at: datetime


class Ticket(BaseModel):
    id: UUID
    reference: str
    subject: str
    status: SupportStatus
    version: int
    requester_email: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    first_response_due_at: datetime | None = None
    first_responded_at: datetime | None = None
    first_response_overdue: bool = False
    support_hours: str = "Пн–пт, 10:00–19:00 МСК"
    archived: bool
    source_path: str | None
    assigned_admin_email: str | None
    message_count: int
    last_message_preview: str


class TicketDetail(Ticket):
    messages: list[Message]


class TicketList(BaseModel):
    items: list[Ticket]
    total: int


class AdminTicketList(TicketList):
    new_count: int
