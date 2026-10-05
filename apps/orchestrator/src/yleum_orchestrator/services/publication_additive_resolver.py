"""Bound verified catalogs to a preparatory nullable-column plan; execute no SQL."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from yleum_orchestrator.services.publication_additive_plan import (
    AdditivePlan,
    AdditivePlanError,
    MigrationBinding,
    plan_additive,
)

_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TYPES = frozenset(
    {
        "uuid",
        "text",
        "bool",
        "int2",
        "int4",
        "int8",
        "float4",
        "float8",
        "numeric",
        "varchar",
        "date",
        "timestamp",
        "timestamptz",
        "jsonb",
    }
)


@dataclass(frozen=True)
class CatalogColumn:
    name: str
    sql_type: str
    nullable: bool
    atttypmod: int
    default_sha256: str | None = None
    generated: bool = False
    identity: bool = False
    collation_sha256: str | None = None


@dataclass(frozen=True)
class CatalogTable:
    schema: str
    name: str
    columns: tuple[CatalogColumn, ...]
    constraints_sha256: str
    indexes_sha256: str
    policies_sha256: str
    triggers_sha256: str
    persistence: str
    plain_heap: bool
    rls_enabled: bool
    rls_forced: bool


@dataclass(frozen=True)
class CatalogReceipt:
    kind: str
    binding_sha256: str
    schema_sha256: str
    data_contract_sha256: str
    catalog_sha256: str
    tables: tuple[CatalogTable, ...]
    event_triggers_absent: bool


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def binding_digest(binding: MigrationBinding) -> str:
    return _digest(binding.projection())


def catalog_digest(tables: tuple[CatalogTable, ...]) -> str:
    _validate_catalog(tables)
    return _digest([asdict(table) for table in tables])


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise AdditivePlanError(code)


def _name(value: object) -> None:
    _need(
        type(value) is str
        and _NAME.fullmatch(value) is not None
        and not value.startswith("__omnia"),
        "migration_catalog_name_unsupported",
    )


def _hash(value: object) -> None:
    _need(
        type(value) is str and _HASH.fullmatch(value) is not None, "migration_catalog_hash_invalid"
    )


def _validate_catalog(tables: tuple[CatalogTable, ...]) -> None:
    _need(type(tables) is tuple and 0 < len(tables) <= 256, "migration_catalog_shape")
    names = []
    for table in tables:
        _need(
            type(table) is CatalogTable
            and table.schema == "public"
            and table.persistence == "p"
            and table.plain_heap is True
            and type(table.rls_enabled) is bool
            and type(table.rls_forced) is bool,
            "migration_catalog_relation_unsupported",
        )
        _name(table.name)
        names.append(table.name)
        for digest in (
            table.constraints_sha256,
            table.indexes_sha256,
            table.policies_sha256,
            table.triggers_sha256,
        ):
            _hash(digest)
        _need(
            type(table.columns) is tuple and 0 < len(table.columns) <= 256,
            "migration_catalog_shape",
        )
        columns = []
        for column in table.columns:
            _need(type(column) is CatalogColumn, "migration_catalog_shape")
            _name(column.name)
            _need(
                type(column.atttypmod) is int and column.atttypmod == -1,
                "migration_catalog_typmod_unsupported",
            )
            _need(
                type(column.sql_type) is str
                and column.sql_type in {"pg_catalog." + t for t in _TYPES}
                and type(column.nullable) is bool
                and type(column.generated) is bool
                and type(column.identity) is bool,
                "migration_catalog_column_unsupported",
            )
            for optional_digest in (column.default_sha256, column.collation_sha256):
                if optional_digest is not None:
                    _hash(optional_digest)
            columns.append(column.name)
        _need(len(set(columns)) == len(columns), "migration_catalog_shape")
    _need(names == sorted(set(names)), "migration_catalog_shape")


def resolve_additive(
    binding: MigrationBinding,
    live: CatalogReceipt,
    source: CatalogReceipt,
    *,
    backup_verified: bool,
) -> AdditivePlan:
    """Resolve only verified, exact-bound nullable additions; never execute them.

    Receipts must be supplied by trusted controller observers, not client fields.
    Schema hashes retain the existing pg_dump algorithm; catalog hashes have a
    separate normalized identity. Neither is substituted for the other.
    """
    _need(backup_verified is True, "migration_verified_backup_required")
    bound = binding_digest(binding)
    for receipt, kind, schema in (
        (live, "published", binding.live_schema_sha256),
        (source, "source", binding.target_schema_sha256),
    ):
        _need(
            type(receipt) is CatalogReceipt
            and receipt.kind == kind
            and receipt.event_triggers_absent is True
            and receipt.binding_sha256 == bound
            and receipt.schema_sha256 == schema
            and receipt.data_contract_sha256 == binding.data_contract_sha256,
            "migration_catalog_binding_changed",
        )
        _need(
            receipt.catalog_sha256 == catalog_digest(receipt.tables),
            "migration_catalog_content_changed",
        )
    _need(
        tuple(t.name for t in live.tables) == tuple(t.name for t in source.tables),
        "migration_relation_set_changed",
    )
    statements = []
    for old, new in zip(live.tables, source.tables, strict=True):
        _need(
            (
                old.schema,
                old.name,
                old.persistence,
                old.plain_heap,
                old.rls_enabled,
                old.rls_forced,
                old.constraints_sha256,
                old.indexes_sha256,
                old.policies_sha256,
                old.triggers_sha256,
            )
            == (
                new.schema,
                new.name,
                new.persistence,
                new.plain_heap,
                new.rls_enabled,
                new.rls_forced,
                new.constraints_sha256,
                new.indexes_sha256,
                new.policies_sha256,
                new.triggers_sha256,
            ),
            "migration_relation_semantics_changed",
        )
        _need(
            len(new.columns) >= len(old.columns) and new.columns[: len(old.columns)] == old.columns,
            "migration_existing_columns_changed",
        )
        for column in new.columns[len(old.columns) :]:
            _need(
                column.nullable is True
                and column.default_sha256 is None
                and column.generated is False
                and column.identity is False
                and column.collation_sha256 is None,
                "migration_added_column_effect_unsupported",
            )
            statements.append(
                f"ALTER TABLE public.{old.name} ADD COLUMN {column.name} {column.sql_type}"
            )
    _need(0 < len(statements) <= 64, "migration_diff_empty_or_overbudget")
    return plan_additive(
        binding,
        ";".join(statements),
        {t.name: tuple(c.name for c in t.columns) for t in live.tables},
    )
