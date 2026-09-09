"""Backward writes must not erase fields that a historical release did not know."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from omnia_orchestrator.services.restoration_data_contract import (
    DataContract,
    assess_contract,
    database_policy_sql,
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


def test_additive_nullable_surname_is_retained_and_not_writable_by_old_release():
    old = DataContract.model_validate(contract())
    current = contract()
    current["tables"][0]["columns"].append({"name": "surname", "type": "text"})
    result = assess_contract(old, DataContract.model_validate(current))
    assert result.blockers == []
    assert result.retained_columns == ["customers.surname"]
    sql = database_policy_sql(
        old, epoch=7, project_id="project", token_secret="secret", password="pass"
    )
    assert "pg_constraint" in sql and "k.contype IN ('p','f')" in sql
    assert "a.attname=ANY(ARRAY['id','name']::text[])" in sql
    assert result.blocked_deletes == ["customers"]
    assert '"surname"' not in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "NOBYPASSRLS" in sql
    assert "token_secret" in sql


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


def test_sql_quotes_private_values_and_does_not_grant_owner_or_ddl_rights():
    sql = database_policy_sql(
        DataContract.model_validate(contract()),
        epoch=7,
        project_id="p'1",
        token_secret="s'2",
        password="p'3",
    )
    assert "'p''1'" in sql and "'s''2'" in sql and "'p''3'" in sql
    assert "REVOKE ALL ON DATABASE" in sql
    assert "REVOKE ALL ON ALL FUNCTIONS" in sql
    assert "REVOKE ALL ON ALL TABLES" in sql
    assert "ALTER DEFAULT PRIVILEGES" in sql
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in sql
    assert "hmac(" in sql
    assert "expires_at" in sql


def test_json_unknown_fields_are_preserved_by_controller_trigger():
    data = contract()
    data["tables"][0]["columns"].append({"name": "profile", "type": "jsonb", "json_keys": ["name"]})
    old = DataContract.model_validate(data)
    data["tables"][0]["columns"][-1]["json_keys"].append("surname")
    assert not assess_contract(old, DataContract.model_validate(data)).blockers
    sql = database_policy_sql(old, epoch=1, project_id="p", token_secret="s", password="p")
    assert "BEFORE INSERT OR UPDATE" in sql
    assert 'OLD."profile"' in sql and 'NEW."profile"' in sql
    assert "jsonb_typeof" in sql
