"""Backward writes must not erase fields that a historical release did not know."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from omnia_orchestrator.services.restoration_data_contract import (
    DataContract,
    assess_contract,
)


def contract():
    return {
        "version": 1,
        "tables": [
            {
                "name": "customers",
                "owner_column": "owner_max_user_id",
                "columns": [
                    {"name": "id", "type": "uuid", "nullable": False},
                    {"name": "owner_max_user_id", "type": "text", "nullable": False},
                    {"name": "name", "type": "text", "nullable": False},
                ],
            }
        ],
    }


def test_additive_nullable_surname_is_retained_for_old_release():
    old = DataContract.model_validate(contract())
    current = contract()
    current["tables"][0]["columns"].append({"name": "surname", "type": "text"})
    result = assess_contract(old, DataContract.model_validate(current))
    assert result.blockers == []
    assert result.retained_columns == ["customers.surname"]
    assert result.blocked_deletes == ["customers"]


@pytest.mark.parametrize("change", ["required", "rename", "type", "meaning", "enum", "check"])
def test_incompatible_contract_is_not_green(change):
    old = contract()
    current = deepcopy(old)
    column = current["tables"][0]["columns"][2]
    if change == "required":
        current["tables"][0]["columns"].append(
            {"name": "surname", "type": "text", "nullable": False}
        )
    elif change == "rename":
        column["name"] = "full_name"
    elif change == "type":
        column["type"] = "integer"
    elif change == "meaning":
        column["meaning"] = "money-cents"
    elif change == "enum":
        column["values"] = ["new", "unknown"]
    else:
        current["tables"][0]["checks"] = ["name <> ''"]
    assert assess_contract(
        DataContract.model_validate(old), DataContract.model_validate(current)
    ).blockers


def test_unknown_cascade_target_requires_adaptation():
    current = contract()
    current["tables"].append(
        {
            "name": "orders",
            "owner_column": "owner_max_user_id",
            "columns": [
                {"name": "id", "type": "uuid"},
                {"name": "owner_max_user_id", "type": "text"},
                {"name": "customer_id", "type": "uuid"},
            ],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "table": "customers",
                    "target": "id",
                    "on_delete": "CASCADE",
                }
            ],
        }
    )
    result = assess_contract(
        DataContract.model_validate(contract()), DataContract.model_validate(current)
    )
    assert "customers" in result.blocked_deletes


@pytest.mark.parametrize(
    "path", ["x; DROP ROLE postgres", "public.customers", '"users"', "pg_authid"]
)
def test_identifiers_cannot_escape_contract_schema(path):
    data = contract()
    data["tables"][0]["name"] = path
    with pytest.raises(ValidationError):
        DataContract.model_validate(data)


def test_contract_cannot_claim_missing_owner_or_duplicate_fields():
    for change in ("owner", "duplicate"):
        data = contract()
        if change == "owner":
            data["tables"][0]["owner_column"] = "missing"
        else:
            data["tables"][0]["columns"].append(data["tables"][0]["columns"][0])
        with pytest.raises(ValidationError):
            DataContract.model_validate(data)


def test_declared_json_key_growth_is_compatible():
    data = contract()
    data["tables"][0]["columns"].append({"name": "profile", "type": "jsonb", "json_keys": ["name"]})
    old = DataContract.model_validate(data)
    data["tables"][0]["columns"][-1]["json_keys"].append("surname")
    assert not assess_contract(old, DataContract.model_validate(data)).blockers


def test_undeclared_unchanged_json_column_does_not_block_plain_restoration():
    # Plain project databases have no declared JSON key contract; every MAX kit
    # schema has jsonb columns, so treating "unknown keys" as a blocker would
    # make every historical restore impossible.
    data = contract()
    data["tables"][0]["columns"].append({"name": "details", "type": "jsonb", "nullable": False})
    old = DataContract.model_validate(data)
    assert assess_contract(old, DataContract.model_validate(deepcopy(data))).blockers == []


def test_declared_json_key_loss_still_blocks():
    data = contract()
    data["tables"][0]["columns"].append(
        {"name": "profile", "type": "jsonb", "json_keys": ["name", "surname"]}
    )
    old = DataContract.model_validate(data)
    data["tables"][0]["columns"][-1]["json_keys"] = ["name"]
    assert assess_contract(old, DataContract.model_validate(data)).blockers == [
        "json_write_contract_missing:customers.profile"
    ]
