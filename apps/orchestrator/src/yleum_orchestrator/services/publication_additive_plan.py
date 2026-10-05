"""Prepare a narrowly additive, exact-bound plan. This module executes no SQL.

A plan is not a migration receipt or authorization to bypass publication schema
checks. The caller must first obtain trusted catalog/backup/identity evidence;
transactional execution and durable observed results belong to the controller.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import cast
from uuid import UUID

from pglast import ast, enums, parse_sql

_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TYPES = frozenset(
    (
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
    )
)


class AdditivePlanError(ValueError):
    """Static failures: never interpolate SQL, credentials or customer values."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise AdditivePlanError(code)


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class MigrationBinding:
    owner_id: UUID
    project_id: UUID
    snapshot_id: UUID
    source_revision: str
    build_sha256: str
    pvc_uid: UUID
    epoch: int
    live_schema_sha256: str
    target_schema_sha256: str
    data_contract_sha256: str
    backup_sha256: str

    def projection(self) -> dict[str, object]:
        values = asdict(self)
        for name in ("owner_id", "project_id", "snapshot_id", "pvc_uid"):
            _need(
                type(values[name]) is UUID and values[name].int != 0, "migration_identity_invalid"
            )
            values[name] = str(values[name])
        _need(type(self.epoch) is int and self.epoch >= 0, "migration_fence_invalid")
        _need(
            isinstance(self.source_revision, str)
            and re.fullmatch(r"[0-9a-f]{40}", self.source_revision) is not None,
            "migration_revision_invalid",
        )
        for name in (
            "build_sha256",
            "live_schema_sha256",
            "target_schema_sha256",
            "data_contract_sha256",
            "backup_sha256",
        ):
            _need(
                isinstance(values[name], str) and _HASH.fullmatch(values[name]) is not None,
                "migration_digest_invalid",
            )
        return values


@dataclass(frozen=True)
class AdditivePlan:
    binding: MigrationBinding
    statements: tuple[str, ...]
    catalog: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def catalog_sha256(self) -> str:
        return _sha(dict(self.catalog))

    @property
    def sha256(self) -> str:
        return _sha(
            {
                "version": 1,
                "binding": self.binding.projection(),
                "statements": self.statements,
                "catalog_sha256": self.catalog_sha256,
            }
        )

    def summary(self) -> dict[str, object]:
        return {
            "kind": "additive_preparation_only",
            "statement_count": len(self.statements),
            "plan_sha256": self.sha256,
            "execution_authorized": False,
        }


def _name(value: object) -> str:
    _need(
        isinstance(value, str)
        and _NAME.fullmatch(value) is not None
        and not value.startswith("__omnia"),
        "migration_object_name_unsupported",
    )
    return cast(str, value)


def _relation(relation: object) -> str:
    if not isinstance(relation, ast.RangeVar):
        raise AdditivePlanError("migration_relation_unsupported")
    _need(
        isinstance(relation, ast.RangeVar)
        and relation.catalogname is None
        and relation.schemaname in (None, "public")
        and relation.relpersistence == "p"
        and relation.alias is None
        and relation.inh is True,
        "migration_relation_unsupported",
    )
    return _name(relation.relname)


def _column(column: object, *, existing: bool) -> tuple[str, str]:
    if not isinstance(column, ast.ColumnDef):
        raise AdditivePlanError("migration_column_unsupported")
    name = _name(column.colname)
    _need(
        not any(
            (
                column.compression,
                column.storage_name,
                column.raw_default,
                column.cooked_default,
                column.identitySequence,
                column.collClause,
                column.fdwoptions,
            )
        )
        and column.storage in (None, "\x00")
        and column.identity in (None, "\x00")
        and column.generated in (None, "\x00"),
        "migration_column_effect_unsupported",
    )
    typ = column.typeName
    if not isinstance(typ, ast.TypeName):
        raise AdditivePlanError("migration_type_unsupported")
    _need(
        isinstance(typ, ast.TypeName)
        and not typ.setof
        and not typ.pct_type
        and not typ.arrayBounds
        and typ.typemod == -1,
        "migration_type_unsupported",
    )
    names = tuple(x.sval for x in (typ.names or ()) if isinstance(x, ast.String))
    _need(
        len(names) == len(typ.names or ())
        and (len(names) == 1 or (len(names) == 2 and names[0] == "pg_catalog"))
        and names[-1] in _TYPES,
        "migration_type_unsupported",
    )
    if typ.typmods:
        _need(
            names[-1] in {"numeric", "varchar", "timestamp", "timestamptz"}
            and len(typ.typmods) <= 2
            and all(
                isinstance(x, ast.A_Const)
                and isinstance(x.val, ast.Integer)
                and isinstance(x.val.ival, int)
                and 0 <= x.val.ival <= 65535
                for x in typ.typmods
            ),
            "migration_type_modifier_unsupported",
        )
    constraints = column.constraints or ()
    allowed = (
        {enums.ConstrType.CONSTR_NULL}
        if existing
        else {
            enums.ConstrType.CONSTR_NULL,
            enums.ConstrType.CONSTR_NOTNULL,
            enums.ConstrType.CONSTR_PRIMARY,
        }
    )
    suffix = []
    for item in constraints:
        _need(
            isinstance(item, ast.Constraint)
            and item.contype in allowed
            and not any(
                (
                    item.conname,
                    item.deferrable,
                    item.initdeferred,
                    item.raw_expr,
                    item.cooked_expr,
                    item.keys,
                    item.including,
                    item.options,
                    item.indexname,
                    item.indexspace,
                    item.access_method,
                    item.where_clause,
                    item.pktable,
                )
            ),
            "migration_constraint_unsupported",
        )
        suffix.append(
            {
                enums.ConstrType.CONSTR_NULL: "NULL",
                enums.ConstrType.CONSTR_NOTNULL: "NOT NULL",
                enums.ConstrType.CONSTR_PRIMARY: "PRIMARY KEY",
            }[item.contype]
        )
    _need(
        len(set(suffix)) == len(suffix) and not ("NULL" in suffix and len(suffix) > 1),
        "migration_constraint_unsupported",
    )
    type_sql = "pg_catalog." + cast(str, names[-1])
    if typ.typmods:
        type_sql += "(" + ", ".join(str(x.val.ival) for x in typ.typmods) + ")"
    return name, " ".join((name, type_sql, *suffix))


def plan_additive(
    binding: MigrationBinding, sql: str, existing_columns: dict[str, tuple[str, ...]]
) -> AdditivePlan:
    binding.projection()
    _need(
        isinstance(sql, str) and 0 < len(sql.encode()) <= 65536 and "\x00" not in sql,
        "migration_sql_budget",
    )
    _need(
        isinstance(existing_columns, dict) and len(existing_columns) <= 256,
        "migration_catalog_budget",
    )
    catalog = {}
    for name, columns in existing_columns.items():
        _name(name)
        _need(
            isinstance(columns, tuple)
            and 0 < len(columns) <= 256
            and len(set(columns)) == len(columns),
            "migration_catalog_shape",
        )
        catalog[name] = set(_name(c) for c in columns)
    try:
        parsed = parse_sql(sql)
    except Exception:
        raise AdditivePlanError("migration_sql_invalid") from None
    _need(0 < len(parsed) <= 64, "migration_statement_budget")
    canonical = []
    for raw in parsed:
        node = raw.stmt
        if isinstance(node, ast.CreateStmt):
            name = _relation(node.relation)
            _need(
                name not in catalog
                and not any(
                    (
                        node.inhRelations,
                        node.partbound,
                        node.partspec,
                        node.ofTypename,
                        node.constraints,
                        node.nnconstraints,
                        node.options,
                        node.tablespacename,
                        node.accessMethod,
                        node.if_not_exists,
                    )
                )
                and node.oncommit == enums.OnCommitAction.ONCOMMIT_NOOP,
                "migration_create_unsupported",
            )
            _need(
                bool(node.tableElts) and len(node.tableElts or ()) <= 256, "migration_column_budget"
            )
            new_columns = [_column(c, existing=False) for c in (node.tableElts or ())]
            _need(
                len({c[0] for c in new_columns}) == len(new_columns), "migration_column_duplicate"
            )
            catalog[name] = {c[0] for c in new_columns}
            canonical.append(
                "CREATE TABLE public." + name + " (" + ", ".join(c[1] for c in new_columns) + ")"
            )
        elif isinstance(node, ast.AlterTableStmt):
            name = _relation(node.relation)
            _need(
                name in catalog
                and node.objtype == enums.ObjectType.OBJECT_TABLE
                and not node.missing_ok
                and bool(node.cmds)
                and len(node.cmds or ()) <= 64,
                "migration_alter_unsupported",
            )
            for command in node.cmds or ():
                _need(
                    command.subtype == enums.AlterTableType.AT_AddColumn
                    and not any(
                        (command.name, command.newowner, command.missing_ok, command.recurse)
                    ),
                    "migration_alter_unsupported",
                )
                column, text = _column(command.def_, existing=True)
                _need(column not in catalog[name], "migration_column_duplicate")
                catalog[name].add(column)
                canonical.append("ALTER TABLE public." + name + " ADD COLUMN " + text)
        else:
            raise AdditivePlanError("migration_statement_unsupported")
    _need(len(canonical) <= 64, "migration_statement_budget")
    return AdditivePlan(
        binding,
        tuple(canonical),
        tuple(sorted((k, tuple(sorted(v))) for k, v in existing_columns.items())),
    )


def authorize_plan(
    plan: AdditivePlan,
    *,
    current: MigrationBinding,
    owner_id: UUID,
    project_id: UUID,
    snapshot_id: UUID,
    acknowledged_plan_sha256: str,
    explicit_migration_intent: bool,
) -> tuple[str, ...]:
    current.projection()
    _need(
        explicit_migration_intent is True
        and current == plan.binding
        and owner_id == current.owner_id
        and project_id == current.project_id
        and snapshot_id == current.snapshot_id
        and acknowledged_plan_sha256 == plan.sha256,
        "migration_plan_confirmation_changed",
    )
    verified = plan_additive(current, ";".join(plan.statements), dict(plan.catalog))
    _need(verified == plan, "migration_plan_content_changed")
    return verified.statements
