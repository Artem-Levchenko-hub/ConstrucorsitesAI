"""Fixed owner-management wire types; business snapshots never expose raw payloads."""

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class ManagementModule(StrEnum):
    catalog = "catalog"
    orders = "orders"
    leads = "leads"
    bookings = "bookings"


class ResourceId(StrEnum):
    orders = "orders"
    leads = "leads"
    bookings = "bookings"


class OwnerStatus(StrEnum):
    new = "new"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


def _validate_modules(modules: Sequence[str | ManagementModule]) -> list[ManagementModule]:
    if isinstance(modules, (str, bytes)) or not isinstance(modules, Sequence):
        raise ValueError("modules must be a sequence of module names")
    selected = [ManagementModule(module) for module in modules]
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate management module")
    if (
        ManagementModule.orders in selected or ManagementModule.bookings in selected
    ) and ManagementModule.catalog not in selected:
        raise ValueError("orders and bookings require catalog")
    return [module for module in ManagementModule if module in selected]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


Revision = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Money = Annotated[StrictInt, Field(ge=0, le=9_007_199_254_740_991)]
Note = Annotated[StrictStr, Field(max_length=2000)]
Cursor = Annotated[StrictStr, Field(min_length=1, max_length=4096)]


def _require_integer(value: object) -> object:
    if type(value) is not int:
        raise ValueError("value must be an integer")
    return value


def _require_datetime_input(value: object) -> object:
    # Parse ISO text before Pydantic can interpret numeric strings as Unix time.
    # A calendar-only ISO string remains naive and is rejected with naive objects.
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is not None and (
        not isinstance(value, datetime) or value.utcoffset() is None
    ):
        raise ValueError("timestamp must be a timezone-aware ISO datetime")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(_require_datetime_input)]
RecordField = Literal[
    "id", "resource", "created_at", "revision", "status", "customer_name",
    "contact_phone", "contact_email", "comment", "requested_for", "service_title",
    "service_price_label", "items", "total_kopecks", "currency", "note",
]


class ResourceFieldDefinition(_WireModel):
    key: RecordField
    label: StrictStr
    kind: Literal["text", "date", "money", "items", "status", "note"]
    readonly: StrictBool
    editable: StrictBool
    owner_only: StrictBool


class ResourceDefinition(_WireModel):
    id: ResourceId
    label: StrictStr
    singular_label: StrictStr
    action_type: Literal["order_created", "lead_created", "booking_created"]
    fields: list[ResourceFieldDefinition] = Field(strict=True)
    status_labels: dict[OwnerStatus, StrictStr] = Field(strict=True)
    transitions: dict[OwnerStatus, list[OwnerStatus]] = Field(strict=True)


class ManagementContract(_WireModel):
    protocol: Annotated[Literal[1], BeforeValidator(_require_integer)]
    modules: list[ManagementModule] = Field(strict=True)
    resources: list[ResourceDefinition] = Field(strict=True)
    contract_hash: Revision

    @field_validator("modules")
    @classmethod
    def valid_modules(cls, value: list[ManagementModule]) -> list[ManagementModule]:
        return _validate_modules(value)


class OwnerRecordItem(_WireModel):
    catalog_id: StrictStr = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    title: StrictStr = Field(min_length=1, max_length=120)
    quantity: StrictInt = Field(ge=1, le=9_007_199_254_740_991)
    unit_price_kopecks: Money
    option: StrictStr | None = Field(default=None, max_length=40)


class OwnerRecordPublic(_WireModel):
    id: UUID
    resource: ResourceId
    created_at: Timestamp
    revision: Revision
    status: OwnerStatus
    customer_name: StrictStr = Field(min_length=1, max_length=120)
    contact_phone: StrictStr | None = Field(default=None, max_length=64)
    contact_email: StrictStr | None = Field(default=None, max_length=254)
    comment: Note | None = None
    requested_for: Timestamp | None = None
    service_title: StrictStr | None = Field(default=None, max_length=120)
    service_price_label: StrictStr | None = Field(default=None, max_length=80)
    items: list[OwnerRecordItem] = Field(default_factory=list, strict=True)
    total_kopecks: Money | None = None
    currency: Literal["RUB"] | None = None
    note: Note | None = None

    @field_validator("created_at", "requested_for")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def coherent_snapshot(self) -> Self:
        if (self.total_kopecks is None) != (self.currency is None):
            raise ValueError("currency RUB is required exactly when an amount is present")
        if self.resource != ResourceId.bookings and (
            self.service_title is not None or self.service_price_label is not None
        ):
            raise ValueError("service snapshot is only valid for booking requests")
        return self


class OwnerRecordsPagePublic(_WireModel):
    records: list[OwnerRecordPublic] = Field(strict=True, max_length=100)
    next_cursor: Cursor | None = None


class OwnerRecordsQuery(_WireModel):
    resource: ResourceId
    limit: StrictInt = Field(default=25, ge=1, le=100)
    cursor: Cursor | None = None
    query: StrictStr = Field(default="", max_length=120)
    status: OwnerStatus | None = None
    created_from: Timestamp | None = None
    created_to: Timestamp | None = None
    requested_from: Timestamp | None = None
    requested_to: Timestamp | None = None

    @model_validator(mode="after")
    def ordered_dates(self) -> Self:
        if self.resource != ResourceId.bookings and (
            self.requested_from is not None or self.requested_to is not None
        ):
            raise ValueError("requested dates require bookings")
        for start, end in ((self.created_from, self.created_to),
                           (self.requested_from, self.requested_to)):
            if start is not None and end is not None and start > end:
                raise ValueError("date range must be ordered")
        return self


class OwnerRecordUpdate(_WireModel):
    expected_revision: Revision
    operation_key: UUID
    status: OwnerStatus | None = None
    note: Note | None = None

    @model_validator(mode="after")
    def has_change(self) -> Self:
        if self.status is None and self.note is None:
            raise ValueError("at least one non-null status or note change is required")
        return self


class ManagementOverviewPublic(_WireModel):
    state: Literal["active", "not_published", "unsupported", "unavailable"]
    configured_modules: list[ManagementModule] = Field(strict=True)
    active_modules: list[ManagementModule] = Field(strict=True)
    resources: list[ResourceDefinition] = Field(strict=True)
    publication_id: UUID | None = None
    contract_hash: Revision | None = None
    catalog_limit: Annotated[Literal[100], BeforeValidator(_require_integer)] = 100

    @field_validator("configured_modules", "active_modules")
    @classmethod
    def valid_modules(cls, value: list[ManagementModule]) -> list[ManagementModule]:
        return _validate_modules(value)
