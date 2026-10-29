"""Canonical, fixed version-1 registry for managed MAX business resources."""

import hashlib
import json
from collections.abc import Sequence

from yleum_api.schemas.max_management import (
    ManagementContract,
    ManagementModule,
    OwnerStatus,
    ResourceDefinition,
    ResourceFieldDefinition,
    ResourceId,
    _validate_modules,
)


def validate_management_modules(
    modules: Sequence[str | ManagementModule],
) -> list[ManagementModule]:
    return _validate_modules(modules)


def _field(
    key: str, label: str, kind: str, *, editable: bool = False, owner_only: bool = False,
) -> ResourceFieldDefinition:
    return ResourceFieldDefinition.model_validate({
        "key": key, "label": label, "kind": kind,
        "readonly": not editable, "editable": editable, "owner_only": owner_only,
    })


def _resource(
    resource: ResourceId, label: str, singular: str, action: str,
    statuses: tuple[str, str, str, str], extra: list[ResourceFieldDefinition],
) -> ResourceDefinition:
    return ResourceDefinition.model_validate({
        "id": resource, "label": label, "singular_label": singular, "action_type": action,
        "fields": [
            _field("created_at", "Создана", "date"),
            _field("status", "Статус", "status", editable=True),
            _field("customer_name", "Клиент", "text"),
            _field("contact_phone", "Телефон", "text"),
            _field("contact_email", "Email", "text"),
            *extra,
            _field("comment", "Комментарий клиента", "text"),
            _field("note", "Заметка владельца", "note", editable=True, owner_only=True),
        ],
        "status_labels": dict(zip(OwnerStatus, statuses, strict=True)),
        # A transition to the current status is handled as a no-op by the writer;
        # these lists contain only actual changes and never reopen terminal states.
        "transitions": {
            "new": ["in_progress", "cancelled"],
            "in_progress": ["completed", "cancelled"],
            "completed": [], "cancelled": [],
        },
    })


_REGISTRY = {
    ResourceId.orders: _resource(
        ResourceId.orders, "Заказы", "Заказ", "order_created",
        ("Новый", "В работе", "Выполнен", "Отменён"),
        [_field("items", "Состав заказа", "items"), _field("total_kopecks", "Сумма", "money")],
    ),
    ResourceId.leads: _resource(
        ResourceId.leads, "Заявки", "Заявка", "lead_created",
        ("Новая", "В работе", "Завершена", "Отменена"), [],
    ),
    ResourceId.bookings: _resource(
        ResourceId.bookings, "Записи", "Запись", "booking_created",
        ("Новая", "Подтверждена", "Завершена", "Отменена"),
        [
            _field("requested_for", "Желаемая дата", "date"),
            _field("service_title", "Услуга", "text"),
            _field("service_price_label", "Стоимость услуги", "text"),
        ],
    ),
}


def get_resource_definition(resource: ResourceId | str) -> ResourceDefinition:
    """Return a detached definition, including all nested mutable metadata."""
    return _REGISTRY[ResourceId(resource)].model_copy(deep=True)


def _contract_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_management_contract(modules: Sequence[str | ManagementModule]) -> ManagementContract:
    ordered = validate_management_modules(modules)
    resources = [
        get_resource_definition(resource) for resource in ResourceId if resource.value in ordered
    ]
    payload = {
        "protocol": 1,
        "modules": [module.value for module in ordered],
        "resources": [resource.model_dump(mode="json") for resource in resources],
    }
    return ManagementContract(
        protocol=1, modules=ordered, resources=resources, contract_hash=_contract_hash(payload),
    )


def parse_management_contract(value: object) -> ManagementContract:
    """Accept only exact registry contracts; a self-signed custom schema grants nothing."""
    if not isinstance(value, dict):
        raise ValueError("management contract must be an object")
    parsed = ManagementContract.model_validate(value)
    expected = build_management_contract(parsed.modules)
    # Comparing the original value also rejects noncanonical list ordering and
    # coerced values. JSON object key ordering does not affect equality or hashing.
    if value != expected.model_dump(mode="json"):
        raise ValueError("management contract does not match the canonical registry and hash")
    return expected
