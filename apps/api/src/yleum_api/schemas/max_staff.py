from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from yleum_api.schemas.max_management import Cursor, Revision

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^\s@]+@[^\s@]+$",
    ),
]
Outcome = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Token = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
OrderMode = Literal["pickup", "yookassa"]


class StaffResource(StrEnum):
    orders = "orders"
    leads = "leads"


class StaffStatus(StrEnum):
    new = "new"
    in_progress = "in_progress"
    ready = "ready"
    completed = "completed"
    cancelled = "cancelled"


class StaffAction(StrEnum):
    claim = "claim"
    ready = "ready"
    hand_over = "hand_over"
    close = "close"
    reassign = "reassign"


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StaffPermissions(_WireModel):
    permissions: list[StaffResource] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def distinct_permissions(self) -> Self:
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("duplicate permissions")
        return self


class StaffInviteCreate(StaffPermissions):
    name: ShortText
    email: Email


class StaffInvitation(StaffInviteCreate):
    id: UUID
    expires_at: datetime


class StaffInviteIssued(_WireModel):
    token: Token
    expires_at: datetime


class StaffInviteAccept(_WireModel):
    token: Token


class StaffInviteAccepted(_WireModel):
    project_id: UUID


class StaffMember(StaffInviteCreate):
    user_id: UUID


class StaffSettings(_WireModel):
    leads_enabled: bool = Field(strict=True)
    orders_mode: OrderMode | None
    prep_minutes: int = Field(ge=1, le=120, strict=True)


class StaffTeam(_WireModel):
    members: list[StaffMember]
    invitations: list[StaffInvitation]
    settings: StaffSettings


class StaffProject(_WireModel):
    project_id: UUID
    project_name: str
    permissions: list[StaffResource]


class StaffAccess(StaffProject):
    actor_id: UUID
    is_owner: bool


class StaffRecordQuery(_WireModel):
    resource: StaffResource
    cursor: Cursor | None = None


class StaffRecordUpdate(_WireModel):
    resource: StaffResource
    action: StaffAction
    expected_revision: Revision
    operation_key: UUID
    outcome: Outcome | None = None
    assignee_id: UUID | None = None

    @model_validator(mode="after")
    def action_fields(self) -> Self:
        if self.action == StaffAction.close:
            if self.resource != StaffResource.leads or self.outcome is None:
                raise ValueError("lead outcome is required")
        elif "outcome" in self.model_fields_set:
            raise ValueError("outcome is only available when closing a lead")
        if ("assignee_id" in self.model_fields_set) != (self.action == StaffAction.reassign):
            raise ValueError("assignee is required only for reassignment")
        return self


class _StaffRecord(_WireModel):
    id: UUID
    created_at: datetime
    revision: Revision
    status: StaffStatus
    comment: str | None
    assignee_id: UUID | None
    assignee_name: str | None
    outcome: str | None


class StaffOrderItem(_WireModel):
    title: str
    quantity: int
    option: str | None


class StaffOrder(_StaffRecord):
    resource: Literal[StaffResource.orders] = StaffResource.orders
    items: list[StaffOrderItem]
    scheduled_for: datetime
    prepare_at: datetime
    payment: Literal["pickup", "paid", "test"]


class StaffLead(_StaffRecord):
    resource: Literal[StaffResource.leads] = StaffResource.leads
    customer_name: str
    contact_phone: str | None
    contact_email: str | None


StaffRecord = Annotated[StaffOrder | StaffLead, Field(discriminator="resource")]


class StaffRecords(_WireModel):
    records: list[StaffRecord]
    next_cursor: Cursor | None
    server_time: datetime


class StaffHistoryEntry(_WireModel):
    actor_name: str
    action: StaffAction
    created_at: datetime
    assignee_name: str | None
    outcome: str | None
