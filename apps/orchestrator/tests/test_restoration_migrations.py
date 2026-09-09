import json

import pytest

from omnia_orchestrator.services.restoration_data_contract import DataContract


def contract():
    return DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "contacts",
                    "owner_column": "owner_id",
                    "primary_key": ["id"],
                    "columns": [
                        {"name": "id", "type": "uuid", "nullable": False},
                        {"name": "owner_id", "type": "text", "nullable": False},
                        {"name": "name", "type": "text", "meaning": "display name"},
                    ],
                }
            ],
        }
    )


def test_additive_plan_adds_nullable_column_and_owned_table_without_rewriting_rows():
    from omnia_orchestrator.services.restoration_migrations import plan_additive_migration

    current = contract()
    desired = current.model_dump(mode="json")
    desired["tables"][0]["columns"].append({"name": "nickname", "type": "text"})
    new = json.loads(json.dumps(desired["tables"][0]))
    new["name"] = "projects"
    for column in new["columns"]:
        column["meaning"] = None
    desired["tables"].append(new)
    plan = plan_additive_migration(current, DataContract.model_validate(desired))
    assert plan.actions == ["add_column:contacts.nickname", "create_table:projects"]
    assert plan.statements[0] == 'ALTER TABLE public."contacts" ADD COLUMN "nickname" text;'
    assert '"id" uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY' in plan.statements[1]
    assert '"owner_id" text NOT NULL' in plan.statements[1]
    assert not any(word in " ".join(plan.statements) for word in ["UPDATE", "DELETE", "TRUNCATE"])
    assert plan_additive_migration(current, current).statements == []


@pytest.mark.parametrize(
    "change",
    [
        "drop_table",
        "drop_column",
        "type",
        "meaning",
        "required",
        "rename",
        "new_json",
        "new_enum",
        "sql_type",
        "new_foreign_key",
        "new_required",
        "new_meaning",
    ],
)
def test_unsupported_change_never_produces_ddl(change):
    from omnia_orchestrator.services.restoration_migrations import plan_additive_migration

    current = contract()
    desired = current.model_dump(mode="json")
    table = desired["tables"][0]
    column = table["columns"][-1]
    if change == "drop_table":
        desired["tables"] = []
    elif change == "drop_column":
        table["columns"].pop()
    elif change == "type":
        column["type"] = "integer"
    elif change == "meaning":
        column["meaning"] = "different meaning"
    elif change == "required":
        column["nullable"] = False
    elif change == "rename":
        column["name"] = "renamed"
    elif change == "new_foreign_key":
        table["foreign_keys"] = [{"column": "id", "table": "contacts", "target": "id"}]
    else:
        extra = {"name": "extra", "type": "text"}
        if change == "new_json":
            extra["type"] = "jsonb"
        elif change == "new_enum":
            extra["values"] = ["one", "two"]
        elif change == "sql_type":
            extra["type"] = "text; DROP TABLE contacts; --"
        elif change == "new_required":
            extra["nullable"] = False
        elif change == "new_meaning":
            extra["meaning"] = "not durably mapped before policy install"
        table["columns"].append(extra)
    with pytest.raises(RuntimeError, match="migration"):
        plan_additive_migration(current, DataContract.model_validate(desired))


def test_transaction_guards_exact_catalog_and_does_not_use_untrusted_dollar_delimiter():
    from omnia_orchestrator.services.restoration_migrations import (
        migration_sql,
        plan_additive_migration,
    )

    current = contract()
    payload = {"tables": [{"comment": "$omnia$'; DROP TABLE contacts; --"}]}
    sql = migration_sql(current, plan_additive_migration(current, current), payload, "project")
    assert sql.startswith("BEGIN;") and sql.endswith("COMMIT;")
    assert "pg_advisory_xact_lock" in sql and 'LOCK TABLE public."contacts"' in sql
    assert "IS DISTINCT FROM" in sql and "migration_catalog_changed" in sql
    assert "DO '" in sql and "DO $omnia$" not in sql


@pytest.mark.parametrize("case", ["no_policy", "newer_policy", "running_writer", "invalid_epoch"])
def test_controller_refuses_unsafe_admission_before_any_sql(monkeypatch, case):
    from types import SimpleNamespace

    from omnia_orchestrator.services import restoration_migrations as module

    policy = None if case == "no_policy" else {"epoch": 9 if case == "newer_policy" else 7}
    monkeypatch.setattr(module, "load_policy", lambda backend: policy)
    monkeypatch.setattr(
        module, "admin_sql", lambda *args: pytest.fail("unsafe admission executed SQL")
    )
    backend = SimpleNamespace(_container=lambda: object() if case == "running_writer" else None)
    with pytest.raises(RuntimeError, match="stopped product"):
        module.apply_additive_contract(
            backend, contract(), expected_epoch=True if case == "invalid_epoch" else 8
        )
