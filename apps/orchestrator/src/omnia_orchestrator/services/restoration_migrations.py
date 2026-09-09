"""Controller-generated additive DDL for an already protected project database.

Caller owns the active generation lease, canonical workspace lock and durable
restart intent. It stops product writers before calling and installs the returned
contract's runtime policy before restarting. SQL and admin credentials never enter
the guest. Ambiguous completion is reconciled by replaying the same declaration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.services.restoration_catalog import CATALOG_SQL, catalog_contract
from omnia_orchestrator.services.restoration_data_contract import (
    DataColumn,
    DataContract,
    DataTable,
    qi,
    ql,
)
from omnia_orchestrator.services.restoration_database import admin_sql, load_policy

SAFE_TYPES = frozenset(
    {
        "uuid",
        "text",
        "boolean",
        "smallint",
        "integer",
        "bigint",
        "real",
        "double precision",
        "numeric",
        "date",
        "time without time zone",
        "timestamp without time zone",
        "timestamp with time zone",
    }
)
OWNER_COLUMNS = frozenset({"owner_id", "owner_max_user_id", "max_user_id", "user_id"})


@dataclass(frozen=True)
class AdditiveMigration:
    statements: list[str]
    actions: list[str]


def _deny(reason: str) -> None:
    raise CellResourceError("migration_unsupported:" + reason)


def _scalar(column: DataColumn) -> str:
    if (
        column.type not in SAFE_TYPES
        or column.values is not None
        or column.json_keys is not None
        or column.meaning is not None
    ):
        _deny("scalar_type_required:" + column.name)
    return qi(column.name) + " " + column.type


def _create_table(table: DataTable) -> str:
    columns = {column.name: column for column in table.columns}
    owner = columns.get(table.owner_column or "")
    identity = columns.get("id")
    if (
        table.read_only
        or table.owner_reference is not None
        or table.owner_column not in OWNER_COLUMNS
        or owner is None
        or owner.type != "text"
        or owner.nullable
        or identity is None
        or identity.type != "uuid"
        or identity.nullable
        or table.primary_key != ["id"]
        or table.unique_keys
        or table.checks
        or table.foreign_keys
    ):
        _deny("new_table_requires_uuid_id_and_direct_owner:" + table.name)
    definitions = []
    for column in table.columns:
        definition = _scalar(column)
        if column.name == "id":
            definition += " NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY"
        elif column.name == table.owner_column:
            definition += " NOT NULL"
        elif not column.nullable:
            _deny("new_required_column:" + table.name + "." + column.name)
        definitions.append(definition)
    return "CREATE TABLE public." + qi(table.name) + " (" + ", ".join(definitions) + ");"


def plan_additive_migration(current: DataContract, desired: DataContract) -> AdditiveMigration:
    before = {table.name: table for table in current.tables}
    after = {table.name: table for table in desired.tables}
    if before.keys() - after.keys():
        _deny("existing_table_omitted")
    statements, actions = [], []
    for table in desired.tables:
        previous = before.get(table.name)
        if previous is None:
            statements.append(_create_table(table))
            actions.append("create_table:" + table.name)
            continue
        if previous.model_dump(exclude={"columns"}) != table.model_dump(exclude={"columns"}):
            _deny("existing_table_contract_changed:" + table.name)
        old_columns = {column.name: column for column in previous.columns}
        new_columns = {column.name: column for column in table.columns}
        if old_columns.keys() - new_columns.keys():
            _deny("existing_column_omitted:" + table.name)
        for column in table.columns:
            old = old_columns.get(column.name)
            if old is not None:
                if old != column:
                    _deny("existing_column_contract_changed:" + table.name + "." + column.name)
                continue
            if not column.nullable:
                _deny("new_required_column:" + table.name + "." + column.name)
            statements.append(
                "ALTER TABLE public." + qi(table.name) + " ADD COLUMN " + _scalar(column) + ";"
            )
            actions.append("add_column:" + table.name + "." + column.name)
    return AdditiveMigration(statements, actions)


def migration_sql(
    current: DataContract,
    plan: AdditiveMigration,
    observed: dict[str, Any],
    project_id: str,
) -> str:
    # Advisory locks are cooperative. The caller also holds the workspace lock,
    # stops all product writers and refuses any unprotected database.
    key = int.from_bytes(hashlib.sha256(project_id.encode()).digest()[:8], "big", signed=True)
    guard = (
        "BEGIN IF ("
        + CATALOG_SQL.strip().removesuffix(";")
        + ")::jsonb IS DISTINCT FROM "
        + ql(json.dumps(observed, ensure_ascii=False))
        + "::jsonb THEN RAISE EXCEPTION USING MESSAGE='migration_catalog_changed'; END IF; END;"
    )
    locks = [
        "LOCK TABLE public." + qi(table.name) + " IN ACCESS EXCLUSIVE MODE;"
        for table in sorted(current.tables, key=lambda table: table.name)
    ]
    return "\n".join(
        [
            "BEGIN;",
            "SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s';",
            f"SELECT pg_advisory_xact_lock({key});",
            *locks,
            "DO " + ql(guard) + ";",
            *plan.statements,
            "COMMIT;",
        ]
    )


def apply_additive_contract(
    backend: Any,
    desired: DataContract,
    *,
    expected_epoch: int,
) -> dict[str, Any]:
    policy = load_policy(backend)
    if (
        type(expected_epoch) is not int
        or expected_epoch < 1
        or policy is None
        or policy["epoch"] > expected_epoch
        or backend._container() is not None
    ):
        raise CellIdentityConflict(
            "migration requires protected database and stopped product writers"
        )
    trusted = DataContract.model_validate(policy["contract"])
    current, blockers = catalog_contract(backend, trusted_contract=trusted)
    if blockers:
        _deny("catalog_requires_review:" + ",".join(blockers))
    plan = plan_additive_migration(current, desired)
    observed = json.loads(admin_sql(backend, CATALOG_SQL))
    # Recheck the catalog represented by the transaction's guard against the
    # same semantic contract used by the planner, not a newer unplanned schema.
    from omnia_orchestrator.services.restoration_catalog import contract_from_catalog

    guarded, blockers = contract_from_catalog(observed, trusted)
    if blockers or guarded != current:
        raise CellIdentityConflict("migration catalog changed before admission")
    admin_sql(backend, migration_sql(current, plan, observed, str(backend.project_id)))
    actual, blockers = catalog_contract(backend, trusted_contract=desired)
    if blockers or plan_additive_migration(actual, desired).actions:
        raise CellIdentityConflict(
            "migration post-commit schema proof mismatch; reconcile declaration"
        )
    return {
        "contract": actual.model_dump(mode="json"),
        "applied_actions": plan.actions,
        "schema_digest": hashlib.sha256(
            json.dumps(actual.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest(),
    }
