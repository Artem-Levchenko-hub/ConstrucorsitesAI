import importlib
import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

FIXTURE = Path(__file__).parent / "fixtures" / "max_management_contract_v1.json"
REVISION = "a" * 64
OPERATION = "c24f9a92-f5c4-4661-91ef-498cf872f1fe"
RECORD = {
    "id": "0fa989f6-051b-43f2-a658-f68ca78788ac",
    "resource": "leads",
    "created_at": "2026-10-29T15:00:00+03:00",
    "revision": REVISION,
    "status": "new",
    "customer_name": "Анна",
}
ITEM = {"catalog_id": "service_1", "title": "Услуга", "quantity": 1, "unit_price_kopecks": 0}


def schemas():
    return importlib.import_module("yleum_api.schemas.max_management")


def service():
    return importlib.import_module("yleum_api.services.max_management_contract")


def canonical_hash(value):
    payload = {key: item for key, item in value.items() if key != "contract_hash"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def test_contract_canonical_fixture_and_ru_registry_are_stable():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = service().build_management_contract(["bookings", "leads", "orders", "catalog"])
    assert actual.model_dump(mode="json") == expected
    assert actual.contract_hash == canonical_hash(expected)
    assert [resource.id.value for resource in actual.resources] == ["orders", "leads", "bookings"]
    assert actual.resources[2].status_labels["in_progress"] == "Подтверждена"
    assert actual.resources[0].transitions["new"] == ["in_progress", "cancelled"]
    assert actual.resources[0].transitions["completed"] == []
    assert actual.resources[0].transitions["cancelled"] == []


def test_parse_accepts_reordered_dictionary_keys_but_preserves_field_order():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    reordered = dict(reversed(list(expected.items())))
    reordered["resources"] = [dict(reversed(list(row.items()))) for row in expected["resources"]]
    assert service().parse_management_contract(reordered).model_dump(mode="json") == expected


@pytest.mark.parametrize("modules", [
    ["unknown"], ["catalog", "catalog"], ["orders"], ["bookings"], [True], "catalog", {"leads"},
])
def test_modules_reject_unknown_duplicate_missing_dependency_and_non_sequences(modules):
    with pytest.raises((ValueError, TypeError, ValidationError)):
        service().validate_management_modules(modules)


def test_modules_are_normalized_without_mutating_caller_and_empty_is_valid():
    modules = ["leads", "catalog"]
    assert service().validate_management_modules(modules) == ["catalog", "leads"]
    assert modules == ["leads", "catalog"]
    empty = service().build_management_contract([]).model_dump(mode="json")
    assert empty["modules"] == empty["resources"] == []
    assert empty["contract_hash"] == canonical_hash(empty)
    assert service().build_management_contract(["catalog"]).resources == []


@pytest.mark.parametrize("mutation", [
    "hash", "extra", "protocol_bool", "protocol_float", "resource", "field", "field_order",
    "modules", "action", "transition",
])
def test_parser_rejects_tampering_even_with_recomputed_hash(mutation):
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if mutation == "hash":
        value["contract_hash"] = "0" * 64
    elif mutation == "extra":
        value["sql"] = "SELECT * FROM users"
    elif mutation == "protocol_bool":
        value["protocol"] = True
    elif mutation == "protocol_float":
        value["protocol"] = 1.0
    elif mutation == "resource":
        value["resources"].pop()
    elif mutation == "field":
        value["resources"][0]["fields"][0]["editable"] = True
    elif mutation == "field_order":
        value["resources"][0]["fields"].reverse()
    elif mutation == "modules":
        value["modules"].reverse()
    elif mutation == "action":
        value["resources"][0]["action_type"] = "private_action"
    else:
        value["resources"][0]["transitions"]["completed"] = ["new"]
    if mutation != "hash":
        value["contract_hash"] = canonical_hash(value)
    with pytest.raises((ValueError, TypeError, ValidationError)):
        service().parse_management_contract(value)


@pytest.mark.parametrize("value", [None, [], "{}", 1, {"protocol": 1}])
def test_parser_rejects_incomplete_or_non_object_contract(value):
    with pytest.raises((ValueError, TypeError, ValidationError)):
        service().parse_management_contract(value)


def test_registry_return_values_cannot_mutate_global_definitions():
    first = service().get_resource_definition("bookings")
    first.fields[0].label = "Изменено"
    first.status_labels["new"] = "Изменено"
    first.transitions["new"].clear()
    fresh = service().get_resource_definition("bookings")
    assert fresh.fields[0].label == "Создана"
    assert fresh.status_labels["new"] == "Новая"
    assert fresh.transitions["new"] == ["in_progress", "cancelled"]
    contract = service().build_management_contract(["catalog", "bookings"])
    contract.resources[0].fields.clear()
    assert service().get_resource_definition("bookings").fields
    with pytest.raises(ValueError):
        service().get_resource_definition("private")


def test_record_preserves_booking_text_price_and_no_fake_lead_amount():
    lead = schemas().OwnerRecordPublic.model_validate(RECORD)
    assert lead.total_kopecks is None
    assert lead.currency is None
    assert lead.items == []
    assert lead.created_at.isoformat() == "2026-10-29T12:00:00+00:00"
    booking = schemas().OwnerRecordPublic.model_validate({
        **RECORD, "resource": "bookings", "requested_for": "2026-11-01T09:00:00Z",
        "service_title": "Консультация", "service_price_label": "По договорённости",
    })
    assert booking.service_price_label == "По договорённости"
    assert booking.total_kopecks is None
    assert booking.model_dump(mode="json")["id"] == RECORD["id"]
    assert schemas().OwnerRecordsPagePublic(records=[booking]).next_cursor is None


@pytest.mark.parametrize("patch", [
    {"revision": "A" * 64}, {"revision": "-1"}, {"revision": 1}, {"revision": "a" * 63},
    {"created_at": "2026-10-29T12:00:00"}, {"created_at": 1000},
    {"requested_for": "2026-11-01T12:00:00"}, {"id": "bad"}, {"status": "private"},
    {"raw_payload": {"max_id": "secret"}}, {"customer_name": "a" * 121},
    {"contact_phone": "1" * 65}, {"contact_email": "a" * 255}, {"comment": "a" * 2001},
    {"note": "a" * 2001}, {"total_kopecks": True}, {"total_kopecks": -1},
    {"total_kopecks": 9007199254740992}, {"total_kopecks": 1.5},
    {"total_kopecks": "100"}, {"currency": "RUB"},
    {"total_kopecks": 100, "currency": "USD"}, {"total_kopecks": 100},
    {"service_price_label": "По запросу"},
])
def test_record_rejects_invalid_or_unbounded_data(patch):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordPublic.model_validate({**RECORD, **patch})


@pytest.mark.parametrize("field", ["created_at", "requested_for"])
@pytest.mark.parametrize("timestamp", ["1000", "20261029", "1000.5", "-1000"])
def test_record_rejects_numeric_timestamp_strings(field, timestamp):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordPublic.model_validate({
            **RECORD, "resource": "bookings", field: timestamp,
        })


@pytest.mark.parametrize("field,value", [
    ("quantity", True), ("quantity", 0), ("quantity", -1), ("quantity", 1.5),
    ("quantity", "1"), ("quantity", 9007199254740992),
    ("unit_price_kopecks", True), ("unit_price_kopecks", -1),
    ("unit_price_kopecks", 1.5), ("unit_price_kopecks", "0"),
    ("unit_price_kopecks", 9007199254740992), ("catalog_id", ""),
])
def test_items_reject_non_integer_negative_or_unsafe_amounts(field, value):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordItem.model_validate({**ITEM, field: value})


def test_items_allow_explicit_zero_price_and_safe_integer_boundary():
    item = schemas().OwnerRecordItem.model_validate(ITEM)
    assert item.unit_price_kopecks == 0
    maximum = schemas().OwnerRecordItem.model_validate({
        **ITEM, "unit_price_kopecks": 9007199254740991,
    })
    assert maximum.unit_price_kopecks == 9007199254740991
    record = schemas().OwnerRecordPublic.model_validate({
        **RECORD, "resource": "orders", "total_kopecks": 0, "currency": "RUB", "items": [ITEM],
    })
    assert record.total_kopecks == 0


@pytest.mark.parametrize("patch", [
    {"status": "invalid"}, {"note": "x" * 2001}, {"raw_payload": {}},
    {"expected_revision": "-1"}, {"expected_revision": "a" * 64 + "\n"},
    {"operation_key": "invalid"},
])
def test_update_rejects_invalid_revision_operation_and_unknown_data(patch):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordUpdate.model_validate({
            "expected_revision": REVISION, "operation_key": OPERATION, "status": "new", **patch,
        })


@pytest.mark.parametrize("patch", [{}, {"status": None}, {"note": None}])
def test_update_requires_non_null_change(patch):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordUpdate.model_validate({
            "expected_revision": REVISION, "operation_key": OPERATION, **patch,
        })


def test_update_preserves_absent_note_and_allows_explicit_clear():
    status = schemas().OwnerRecordUpdate(
        expected_revision=REVISION, operation_key=OPERATION, status="in_progress",
    )
    assert "note" not in status.model_dump(mode="json", exclude_unset=True)
    clear = schemas().OwnerRecordUpdate(
        expected_revision=REVISION, operation_key=OPERATION, note="",
    )
    assert clear.model_dump(mode="json", exclude_unset=True)["note"] == ""
    with pytest.raises(ValidationError):
        schemas().OwnerRecordUpdate(expected_revision=REVISION, status="new")


@pytest.mark.parametrize("patch", [
    {"resource": "private"}, {"limit": 0}, {"limit": 101}, {"limit": True},
    {"limit": "25"}, {"query": "q" * 121}, {"cursor": "c" * 4097},
    {"created_from": "2026-10-29T12:00:00"}, {"created_to": "2026-10-29T12:00:00"},
    {"created_from": "2026-11-01T00:00:00Z", "created_to": "2026-10-01T00:00:00Z"},
    {"sql": "private"},
])
def test_query_is_bounded_and_rejects_invalid_date_ranges(patch):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordsQuery.model_validate({"resource": "leads", **patch})


@pytest.mark.parametrize("field", ["created_from", "created_to"])
@pytest.mark.parametrize("timestamp", ["1000", "20261029", "1000.5", "-1000"])
def test_query_rejects_numeric_timestamp_strings(field, timestamp):
    with pytest.raises(ValidationError):
        schemas().OwnerRecordsQuery.model_validate({"resource": "leads", field: timestamp})


def test_query_defaults_and_valid_aware_range():
    query = schemas().OwnerRecordsQuery(
        resource="leads", created_from="2026-10-29T12:00:00Z",
        created_to="2026-11-01T12:00:00+03:00",
    )
    assert query.limit == 25
    assert query.query == ""
    assert query.status is None


def test_overview_describes_honest_states_and_fixed_catalog_limit():
    for state in ("active", "not_published", "unsupported", "unavailable"):
        result = schemas().ManagementOverviewPublic(
            state=state, configured_modules=["catalog", "leads"], active_modules=[], resources=[],
        )
        assert result.model_dump(mode="json")["catalog_limit"] == 100
        assert result.publication_id is None
        assert result.contract_hash is None
    with pytest.raises(ValidationError):
        schemas().ManagementOverviewPublic(
            state="active", configured_modules=[], active_modules=[], resources=[],
            catalog_limit=101,
        )


@pytest.mark.parametrize("patch", [
    {"catalog_limit": 100.0}, {"contract_hash": "A" * 64},
    {"publication_id": "not-a-uuid"}, {"state": "unknown"},
    {"active_modules": ["orders"]}, {"configured_modules": ["leads", "leads"]},
    {"raw_payload": {}},
])
def test_overview_rejects_invalid_identity_capabilities_and_numeric_coercion(patch):
    with pytest.raises(ValidationError):
        schemas().ManagementOverviewPublic.model_validate({
            "state": "active", "configured_modules": [], "active_modules": [],
            "resources": [], **patch,
        })
