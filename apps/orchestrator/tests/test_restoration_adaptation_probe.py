from __future__ import annotations

import json

import pytest

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.services.restoration_adaptation_probe import validate_probe_contract


def test_initial_witness_hints_use_only_observed_identifiers_and_omit_defaults():
    from yleum_orchestrator.services.restoration_adaptation_probe import initial_witness_hints
    from yleum_orchestrator.services.restoration_data_contract import DataContract

    raw = _contract()
    raw["tables"][0]["columns"].extend(
        [
            {
                "name": "completed",
                "type": "boolean",
                "nullable": False,
                "meaning": "PRIVATE_BUSINESS_VALUE",
            },
            {"name": "created_at", "type": "timestamp", "nullable": False, "default": "now()"},
            {
                "name": "secret_note",
                "type": "text",
                "nullable": True,
                "default": "'PRIVATE_DEFAULT_VALUE'::text",
            },
        ]
    )
    hints = initial_witness_hints(DataContract.model_validate(raw))
    assert hints == [
        "correct witness for orders id id owner max_user_id value probe_value "
        "create_values completed"
    ]
    assert "PRIVATE" not in str(hints)


@pytest.mark.parametrize(
    "unsupported", ["read_only", "no_owner", "non_uuid", "composite", "no_text"]
)
def test_initial_witness_hints_do_not_advertise_unsupported_tables(unsupported):
    from yleum_orchestrator.services.restoration_adaptation_probe import initial_witness_hints
    from yleum_orchestrator.services.restoration_data_contract import DataContract

    raw = _contract()
    table = raw["tables"][0]
    if unsupported == "read_only":
        table["read_only"] = True
    elif unsupported == "no_owner":
        table["owner_column"] = None
    elif unsupported == "non_uuid":
        table["columns"][0]["type"] = "integer"
    elif unsupported == "composite":
        table["primary_key"] = ["id", "max_user_id"]
    else:
        table["columns"][2]["type"] = "boolean"
    assert initial_witness_hints(DataContract.model_validate(raw)) == []


def test_initial_witness_hints_bound_whole_examples_without_truncating_identifiers():
    from copy import deepcopy

    from yleum_orchestrator.services.restoration_adaptation_probe import initial_witness_hints
    from yleum_orchestrator.services.restoration_data_contract import DataContract

    table = _contract()["tables"][0]
    tables = [{**deepcopy(table), "name": f"entity_{i:02d}"} for i in range(40)]
    tables[0]["columns"].extend(
        [{"name": f"required_{i:02d}", "type": "text", "nullable": False} for i in range(60)]
    )
    hints = initial_witness_hints(DataContract.model_validate({"version": 1, "tables": tables}))
    assert len(hints) == 32
    assert all(len(hint) <= 600 for hint in hints)
    assert not any("entity_00 " in hint for hint in hints)


def _contract(*, include_visits: bool = False) -> dict[str, object]:
    tables: list[dict[str, object]] = [
        {
            "name": "orders",
            "columns": [
                {
                    "name": "id",
                    "type": "uuid",
                    "nullable": False,
                    "default": "gen_random_uuid()",
                },
                {"name": "max_user_id", "type": "text", "nullable": False},
                {"name": "probe_value", "type": "text", "nullable": False},
            ],
            "owner_column": "max_user_id",
            "primary_key": ["id"],
        }
    ]
    if include_visits:
        tables.append(
            {
                "name": "visits",
                "columns": [
                    {
                        "name": "id",
                        "type": "uuid",
                        "nullable": False,
                        "default": "gen_random_uuid()",
                    },
                    {"name": "max_user_id", "type": "text", "nullable": False},
                    {"name": "probe_value", "type": "text", "nullable": False},
                ],
                "owner_column": "max_user_id",
                "primary_key": ["id"],
            }
        )
    return {"version": 1, "tables": tables}


def _files(**updates: object) -> dict[str, str]:
    manifest: dict[str, object] = {
        "version": 1,
        "endpoint": "/api/orders/restoration-probe",
        "witnesses": [
            {
                "entity": "orders",
                "id_column": "id",
                "owner_column": "max_user_id",
                "value_column": "probe_value",
                "create_values": {},
            }
        ],
        "max_payload_bytes": 4096,
    }
    manifest.update(updates)
    return {".omnia/restoration-probe.json": json.dumps(manifest)}


def test_validate_probe_contract_binds_route_witnesses_and_stable_digest() -> None:
    first = validate_probe_contract(_files(), _contract(), changed_entities={"orders"})
    second = validate_probe_contract(_files(), _contract(), changed_entities=["orders"])

    assert first == second
    assert first.endpoint == "/api/orders/restoration-probe"
    assert first.pointers.item_id == "/item/id"
    assert len(first.contract_digest) == 64
    assert len(first.data_contract_digest) == 64


@pytest.mark.parametrize("endpoint", ["/api/omnia/actions", "/api/max/users", "https://x/a"])
def test_validate_probe_contract_rejects_non_product_routes(endpoint: str) -> None:
    with pytest.raises((CellIdentityConflict, ValueError)):
        validate_probe_contract(_files(endpoint=endpoint), _contract())


def test_validate_probe_contract_requires_every_changed_business_entity() -> None:
    with pytest.raises(CellIdentityConflict, match="misses a changed entity"):
        validate_probe_contract(
            _files(), _contract(include_visits=True), changed_entities={"orders", "visits"}
        )


def test_validate_probe_contract_rejects_witness_not_matching_data_contract() -> None:
    witness = {
        "entity": "orders",
        "id_column": "id",
        "owner_column": "attacker_id",
        "value_column": "probe_value",
        "create_values": {},
    }
    with pytest.raises(CellIdentityConflict, match="owner column must be"):
        validate_probe_contract(_files(witnesses=[witness]), _contract())


def test_validate_probe_contract_rejects_unbounded_or_unknown_manifest_fields() -> None:
    with pytest.raises(CellIdentityConflict, match="manifest is invalid"):
        validate_probe_contract(_files(extra_pointer="/secret"), _contract())


def test_common_qa_tasks_contract_is_probeable_with_one_text_value_column() -> None:
    contract = {
        "version": 1,
        "tables": [
            {
                "name": "qa_tasks",
                "columns": [
                    {
                        "name": "id",
                        "type": "uuid",
                        "nullable": False,
                        "default": "gen_random_uuid()",
                    },
                    {"name": "user_id", "type": "text", "nullable": False},
                    {"name": "title", "type": "text", "nullable": False},
                    {"name": "completed", "type": "boolean", "nullable": False},
                    {
                        "name": "created_at",
                        "type": "timestamp with time zone",
                        "nullable": False,
                        "default": "now()",
                    },
                ],
                "owner_column": "user_id",
                "primary_key": ["id"],
            }
        ],
    }
    files = _files(
        endpoint="/api/qa-tasks/restoration-probe",
        witnesses=[
            {
                "entity": "qa_tasks",
                "id_column": "id",
                "owner_column": "user_id",
                "value_column": "title",
                "create_values": {"completed": False},
            }
        ],
    )

    probe = validate_probe_contract(files, contract, changed_entities={"qa_tasks"})

    assert probe.witnesses[0].value_column == "title"
    assert probe.witnesses[0].create_values == {"completed": False}


def test_probe_rejects_payload_cap_smaller_than_required_create_envelope() -> None:
    contract = _contract()
    columns = contract["tables"][0]["columns"]  # type: ignore[index]
    columns.append(  # type: ignore[union-attr]
        {"name": "required_note", "type": "text", "nullable": False}
    )
    witness = {
        "entity": "orders",
        "id_column": "id",
        "owner_column": "max_user_id",
        "value_column": "probe_value",
        "create_values": {"required_note": "x" * 1500},
    }

    with pytest.raises(CellIdentityConflict, match="payload cap"):
        validate_probe_contract(
            _files(witnesses=[witness], max_payload_bytes=1024), contract
        )


def test_probe_rejects_caps_below_streaming_safety_minimum() -> None:
    with pytest.raises(CellIdentityConflict, match="manifest is invalid"):
        validate_probe_contract(_files(max_payload_bytes=256), _contract())
