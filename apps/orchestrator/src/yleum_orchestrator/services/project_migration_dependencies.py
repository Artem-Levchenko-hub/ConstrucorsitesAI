"""Read-only early FK feedback; PostgreSQL execution remains final authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from typing import Any

from pglast import parse_sql
from pglast.parser import ParseError

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.restoration_database import admin_sql

CATALOG_SQL = """
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
SET LOCAL statement_timeout = '3s';
SET LOCAL lock_timeout = '1s';
DO $catalog$
DECLARE ledger json := '{}';
BEGIN
 IF pg_catalog.to_regclass('public.__omnia_project_migrations') IS NOT NULL THEN
  SELECT coalesce(pg_catalog.json_object_agg(name, sha256), '{}'::json) INTO ledger
   FROM public.__omnia_project_migrations;
 END IF;
 PERFORM pg_catalog.set_config('omnia.pending_migration_catalog', ledger::text, true);
END $catalog$;
SELECT pg_catalog.json_build_object(
 'relations', coalesce((SELECT pg_catalog.json_agg(pg_catalog.json_build_array(n.nspname,c.relname))
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind IN ('r','p') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'), '[]'),
 'applied', pg_catalog.current_setting('omnia.pending_migration_catalog')::json);
COMMIT;
"""


def _nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _nodes(child)


def _relation(node: Mapping[str, Any]) -> tuple[str, str]:
    return str(node.get("schemaname") or "public"), str(node["relname"])


def dependency_gap(
    migrations: Mapping[str, str],
    relations: set[tuple[str, str]],
    applied: Mapping[str, str],
) -> str | None:
    """Prove only simple ordered CREATE/ALTER dependencies; defer anything else.

    This is feedback, not an SQL acceptance proof. Dynamic DDL, custom search
    paths and parser-version differences must not falsely reject valid SQL.
    Applied SQL must match the journal before pending dependency feedback.
    """
    for path, digest in applied.items():
        if path not in migrations:
            return f"Applied migration {path} is missing; restore its exact SQL."
        if digest != hashlib.sha256(migrations[path].encode()).hexdigest():
            return f"Applied migration {path} checksum changed; restore its exact SQL."
    statements = []
    for path, source in sorted(migrations.items()):
        if path in applied:
            continue
        try:
            for raw in parse_sql(source):
                statement = raw(skip_none=True)["stmt"]
                if statement["@"] not in {
                    "CreateStmt",
                    "AlterTableStmt",
                    "IndexStmt",
                    "CreateSchemaStmt",
                    "CreateEnumStmt",
                    "CommentStmt",
                }:
                    return None
                if statement["@"] == "CreateSchemaStmt" and statement.get("schemaElts"):
                    return None
                if statement.get("relation", {}).get("relpersistence") == "t":
                    return None
                statements.append((path, statement))
        except (ParseError, ValueError, RecursionError):
            return None

    available = set(relations)
    for path, statement in statements:
        if statement["@"] == "CreateStmt":
            relation = _relation(statement["relation"])
            if statement.get("if_not_exists") and relation in available:
                continue  # PostgreSQL does not evaluate this skipped definition.
            available.add(relation)  # Self-referencing foreign keys are valid.
        if statement["@"] not in {"CreateStmt", "AlterTableStmt"}:
            continue
        for node in _nodes(statement):
            parent = node.get("pktable") if node.get("@") == "Constraint" else None
            if parent and _relation(parent) not in available:
                schema, name = _relation(parent)
                return (
                    f"Pending migration {path} references absent product table "
                    f"{json.dumps(schema + '.' + name)}. No SQL was applied. "
                    "Define a product-owned parent in an earlier migration, or store the "
                    "trusted MAX subject as text without a core-table foreign key. "
                    "Legacy 0000/0001 and schema.ts exports do not create product DB tables. "
                    "Preserve accepted/applied migrations."
                )
    return None


def check_migration_dependencies(backend: Any, migrations: Mapping[str, str]) -> str | None:
    try:
        catalog = json.loads(
            admin_sql(backend, CATALOG_SQL, max_bytes=128 * 1024, lifetime_seconds=5)
        )
        relations = {tuple(item) for item in catalog["relations"]}
        applied = catalog["applied"]
        if any(len(item) != 2 for item in relations) or not isinstance(applied, dict):
            raise ValueError("invalid controller catalog")
        return dependency_gap(migrations, relations, applied)
    except (ValueError, TypeError, KeyError) as exc:
        raise CellResourceError(
            "product migration catalog is unavailable; no SQL was applied"
        ) from exc
