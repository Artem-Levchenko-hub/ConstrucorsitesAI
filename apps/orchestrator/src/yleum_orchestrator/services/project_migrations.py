"""Controller-owned, transactional project SQL; never imports a product schema."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import uuid5

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.restoration_database import (
    ControllerDatabaseError,
    admin_sql,
    migrator_sql,
)

RECEIPT_PREFIX = "[project-migrations:v1]"
LEGACY_PATHS = ("drizzle/0000_max_core.sql", "drizzle/0001_business_core.sql")
_JOURNAL = "public.__omnia_project_migrations"
ADOPTION_PREFLIGHT_SQL = "BEGIN;\n" + """DO $adoption$
DECLARE item record; populated boolean; visited integer := 0;
BEGIN
 IF pg_catalog.to_regclass('public.__omnia_project_migrations') IS NULL THEN
  FOR item IN SELECT n.nspname, c.relname, c.relkind FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
      AND c.relkind IN ('r','p','f','m') ORDER BY n.nspname,c.relname
  LOOP
    visited := visited + 1;
    IF visited > 256 OR item.relkind IN ('f','m') THEN
      RAISE EXCEPTION 'untracked project database requires explicit reconciliation';
    END IF;
    EXECUTE pg_catalog.format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE',
                              item.nspname,item.relname);
    EXECUTE pg_catalog.format('SELECT EXISTS (SELECT 1 FROM %I.%I LIMIT 1)',
                              item.nspname,item.relname)
      INTO populated;
    IF populated THEN
      RAISE EXCEPTION 'populated project database has no migration journal; reconcile explicitly';
    END IF;
  END LOOP;
 END IF;
END $adoption$;""" + "\nCOMMIT;"

SOURCE_SQL_ERRORS = {
    "42P01": "A referenced relation is absent from the product database",
    "42703": "A referenced column is absent from the product database",
    "42601": "The pending project migration has invalid SQL syntax",
    "42804": "The pending project migration has incompatible SQL types",
    "42830": "The pending foreign key has no matching unique parent key",
    "42704": "A referenced SQL object is absent from the product database",
}


def select_migrations(files: Mapping[str, str], legacy: Mapping[str, str]) -> dict[str, str]:
    selected = {}
    for path, content in sorted(files.items()):
        if not path.startswith("drizzle/") or not path.endswith(".sql"):
            continue
        if len(path.split("/")) != 2:
            raise CellResourceError("project migrations require direct drizzle/*.sql paths")
        if path in LEGACY_PATHS:
            if (
                path not in legacy
                or hashlib.sha256(content.encode()).digest()
                != hashlib.sha256(legacy[path].encode()).digest()
            ):
                raise CellResourceError(
                    "legacy migration identity is ambiguous; separate project SQL"
                )
            continue
        selected[path] = content
    if len(selected) > 256 or sum(len(value.encode()) for value in selected.values()) > 2 * 1024**2:
        raise CellResourceError("project migration inventory exceeds execution budget")
    return selected


def adaptation_database(state: Any, metadata: Mapping[str, Any]) -> bool:
    run_id = state.active_generation_run_id
    marker = metadata.get("restoration_adaptation_run_id")
    if marker == str(run_id) and run_id is not None:
        return True
    # A retained environment can later receive an ordinary generation lease.
    # An old run marker never turns that new run into an adaptation.
    # Candidates prepared before the marker existed retain this controller operation.
    return run_id is not None and any(
        operation.operation_id == uuid5(run_id, "restoration-adaptation-candidate")
        for operation in state.operations
    )


def _text(value: str) -> str:
    # SQL source is data to EXECUTE, never psql input (including backslash commands).
    return f"pg_catalog.convert_from(pg_catalog.decode('{value.encode().hex()}', 'hex'), 'UTF8')"


def migration_sql(
    migrations: Mapping[str, str],
    *,
    verify_only: bool,
    verify_applied: bool = False,
    record_witnessed: bool = False,
    database_identity: str | None = None,
) -> str:
    if record_witnessed and (verify_only or verify_applied):
        raise ValueError("cannot record SQL during read-only verification")
    if database_identity is not None and re.fullmatch(r"[0-9a-f]{64}", database_identity) is None:
        raise ValueError("trusted database identity is invalid")
    identity_expression = ("'" + database_identity + "'" if database_identity is not None else
        "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
        "pg_catalog.current_database() || ':' || "
        "(SELECT system_identifier::pg_catalog.text FROM "
        "pg_catalog.pg_control_system()), 'UTF8')), 'hex')")
    inventory = {path: hashlib.sha256(sql.encode()).hexdigest() for path, sql in migrations.items()}
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    statements = [
        "BEGIN READ ONLY;" if verify_only or verify_applied or not migrations else "BEGIN;",
        "SET LOCAL statement_timeout = '45s';",
        "SET LOCAL lock_timeout = '5s';",
        "SET LOCAL idle_in_transaction_session_timeout = '50s';",
        "SET LOCAL search_path = public, pg_catalog;",
    ]
    if migrations and not verify_only:
        statements.append(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtext('omnia:project:migrations:v1'));"
        )
        if not verify_applied and not record_witnessed:
            statements.append("""
DO $adoption$
DECLARE item record; populated boolean; visited integer := 0;
BEGIN
 IF pg_catalog.to_regclass('public.__omnia_project_migrations') IS NULL THEN
  FOR item IN SELECT n.nspname, c.relname, c.relkind FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
      AND c.relkind IN ('r','p','f','m') ORDER BY n.nspname,c.relname
  LOOP
    visited := visited + 1;
    IF visited > 256 OR item.relkind IN ('f','m') THEN
      RAISE EXCEPTION 'untracked project database requires explicit reconciliation';
    END IF;
    EXECUTE pg_catalog.format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE',
                              item.nspname,item.relname);
    EXECUTE pg_catalog.format('SELECT EXISTS (SELECT 1 FROM %I.%I LIMIT 1)',
                              item.nspname,item.relname)
      INTO populated;
    IF populated THEN
      RAISE EXCEPTION 'populated project database has no migration journal; reconcile explicitly';
    END IF;
  END LOOP;
 END IF;
END $adoption$;
""")
        if not verify_applied:
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {_JOURNAL} "
                "(name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz "
                "NOT NULL DEFAULT pg_catalog.now());"
            )
            # Runtime CRUD defaults apply to business tables, never this
            # controller journal, including while pending SQL is executing.
            statements.append(
                f"REVOKE ALL ON {_JOURNAL} FROM PUBLIC, omnia_project_runtime;"
            )
        blocks = []
        for path, sql in migrations.items():
            name, checksum = _text(path), _text(inventory[path])
            if verify_applied:
                blocks.append(f"""
IF NOT EXISTS (SELECT 1 FROM {_JOURNAL} WHERE name = {name} AND sha256 = {checksum}) THEN
    RAISE EXCEPTION 'project migration application receipt missing or changed';
END IF;
""")
                continue
            blocks.append(f"""
IF EXISTS (SELECT 1 FROM {_JOURNAL} WHERE name = {name}) THEN
    IF NOT EXISTS (SELECT 1 FROM {_JOURNAL} WHERE name = {name} AND sha256 = {checksum}) THEN
        RAISE EXCEPTION 'project migration checksum changed';
    END IF;
ELSE
    {"" if record_witnessed else "EXECUTE " + _text(sql) + ";"}
    PERFORM pg_catalog.set_config('search_path', 'public, pg_catalog', true);
    INSERT INTO {_JOURNAL}(name, sha256) VALUES ({name}, {checksum});
END IF;
""")
        # One transaction covers the entire pending batch. EXECUTE also prevents
        # migration transaction control from escaping the controller transaction.
        statements.append("DO $omnia$ BEGIN\n" + "\n".join(blocks) + "\nEND $omnia$;")
    # Verification/empty inventories never enter the apply branch. Every final
    # receipt must use the same controller-selected resolution, independent of
    # generated routines or SET search_path executed by migration source.
    statements.append("SET LOCAL search_path = pg_catalog, public;")
    statements.append(f"""
WITH catalog AS (
    SELECT 'relation' AS kind, n.nspname || '.' || c.relname AS name,
           pg_catalog.jsonb_build_array(c.relkind, c.relrowsecurity, c.relforcerowsecurity,
             (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(a.attname,
               pg_catalog.format_type(a.atttypid,a.atttypmod),
               a.attnotnull, pg_catalog.pg_get_expr(d.adbin,d.adrelid))
                ORDER BY a.attnum)
              FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
                ON d.adrelid=a.attrelid AND d.adnum=a.attnum
              WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped))
               ::pg_catalog.text AS definition
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    UNION ALL
    SELECT 'constraint', n.nspname || '.' || c.conname, pg_catalog.pg_get_constraintdef(c.oid)
    FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    UNION ALL
    SELECT 'index', n.nspname || '.' || c.relname, pg_catalog.pg_get_indexdef(c.oid)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind='i'
      AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
)
SELECT pg_catalog.json_build_object(
    'contract', 'project-migrations-v1', 'mode', '{"verify_only" if verify_only else "apply"}',
    'source_digest', '{digest}', 'migration_count', {len(migrations)},
    'database_identity', {identity_expression},
    'catalog_digest', pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(COALESCE(
        (SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(kind,name,definition)
                         ORDER BY kind,name,definition)::pg_catalog.text
         FROM catalog), '[]'), 'UTF8')), 'hex')
);
COMMIT;
""")
    return "\n".join(statements)


def run_project_migrations(
    backend: Any,
    migrations: Mapping[str, str],
    *,
    verify_only: bool,
    verify_applied: bool = False,
    record_witnessed: bool = False,
) -> dict[str, Any]:
    try:
        backend.bootstrap_project_database_roles()
        identity = admin_sql(backend,
            "SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
            "pg_catalog.current_database() || ':' || "
            "(SELECT system_identifier::pg_catalog.text FROM "
            "pg_catalog.pg_control_system()), 'UTF8')), 'hex');",
            max_bytes=256, lifetime_seconds=5).decode().strip()
        if re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            raise ValueError("trusted database identity is invalid")
        if migrations and not (verify_only or verify_applied or record_witnessed):
            admin_sql(backend, ADOPTION_PREFLIGHT_SQL, max_bytes=8192, lifetime_seconds=10)
        raw = migrator_sql(
            backend,
            migration_sql(
                migrations,
                verify_only=verify_only,
                verify_applied=verify_applied,
                record_witnessed=record_witnessed,
                database_identity=identity,
            ),
            max_bytes=8192,
            lifetime_seconds=50,
        )
        # The advisory-lock SELECT emits an empty line. Only the controller's
        # final JSON is accepted; PostgreSQL errors/output never reach app logs.
        receipt = json.loads(raw.decode().strip())
        if receipt.get("contract") != "project-migrations-v1" or any(
            len(receipt.get(key, "")) != 64
            for key in ("source_digest", "database_identity", "catalog_digest")
        ):
            raise ValueError("incomplete receipt")
        backend.bootstrap_project_database_roles()
        return dict(receipt)
    except (CellResourceError, ValueError, TypeError, UnicodeError) as exc:
        if (
            isinstance(exc, ControllerDatabaseError)
            and exc.terminated
            and exc.sqlstate in SOURCE_SQL_ERRORS
            and not (verify_only or verify_applied or record_witnessed)
        ):
            raise CellResourceError(
                f"[project-migration-source-error:{exc.sqlstate}] "
                f"{SOURCE_SQL_ERRORS[exc.sqlstate]}. The controller SQL transaction aborted; "
                "no migration batch was committed. Repair pending project SQL and schema, "
                "then rebuild. The product database is separate from managed MAX core: "
                "legacy 0000_max_core.sql/0001_business_core.sql are not applied here; "
                "a schema.ts export does not create max_users or other core tables. "
                "Store the trusted MAX subject as text, or explicitly define a product-owned "
                "parent before its foreign keys. Preserve accepted/applied migrations."
            ) from exc
        raise CellResourceError(
            "project migration verification failed; no safe source-repair receipt is available. "
            "Check controller state, pending SQL and journal checksums. If existing data lacks "
            "a journal, explicit reconciliation is required; do not guess that SQL was applied. "
            "No build was accepted."
        ) from exc


def record_witnessed_project_migrations(backend: Any, executed: Mapping[str, str]) -> None:
    """R0 only: the controller already executed these SQL files and proved their schema.

    Never call for files merely present in source, drizzle push or a custom runner.
    This is internal controller bookkeeping, not an agent/API reconciliation tool.
    """
    from yleum_orchestrator.core.stack_registry import get_stack
    from yleum_orchestrator.services.cell_draft_support import trusted_template_source

    template = trusted_template_source(get_stack("max-miniapp-nextjs").template_dir)
    legacy = {path: (template / path).read_text(encoding="utf-8") for path in LEGACY_PATHS}
    selected = select_migrations(executed, legacy)
    if selected:
        run_project_migrations(backend, selected, verify_only=False, record_witnessed=True)
