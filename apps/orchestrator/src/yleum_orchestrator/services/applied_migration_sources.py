"""Controller-owned SQL witnesses; only the live database journal proves application."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.cell_state import _read_plain_json_file
from yleum_orchestrator.services.project_machine import write_controller_json
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
 PERFORM pg_catalog.set_config('omnia.source_catalog', ledger::text, true);
END $catalog$;
SELECT pg_catalog.json_build_object(
 'database_identity', pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
  pg_catalog.current_database() || ':' ||
  (SELECT system_identifier::pg_catalog.text FROM pg_catalog.pg_control_system()),
  'UTF8')), 'hex'),
 'journal', pg_catalog.to_regclass('public.__omnia_project_migrations') IS NOT NULL,
 'has_relations', EXISTS (SELECT 1 FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE c.relkind IN ('r','p','f','m') AND n.nspname !~ '^pg_'
   AND n.nspname <> 'information_schema'),
 'applied', pg_catalog.current_setting('omnia.source_catalog')::json);
COMMIT;
"""


def _catalog(backend: Any, *, verify_only: bool = False) -> dict[str, Any]:
    try:
        catalog = json.loads(admin_sql(backend, CATALOG_SQL, max_bytes=128 * 1024,
                                       lifetime_seconds=5))
        if (re.fullmatch(r"[0-9a-f]{64}", catalog["database_identity"]) is None
                or not isinstance(catalog["applied"], dict)):
            raise ValueError("invalid catalog")
        for path, digest in catalog["applied"].items():
            if (re.fullmatch(r"drizzle/[^/\\]+\.sql", path) is None
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                raise ValueError("invalid journal entry")
        if not verify_only and not catalog["journal"] and catalog["has_relations"]:
            raise CellResourceError(
                "project database has no migration journal; explicit reconciliation is required"
            )
        return dict(catalog)
    except (ValueError, TypeError, KeyError) as exc:
        raise CellResourceError(
            "applied migration catalog is unavailable; no source was changed"
        ) from exc


def _archive(backend: Any, catalog: Mapping[str, Any]) -> dict[str, str]:
    path = backend.metadata_path.parent / "migration-sources.json"
    try:
        saved = _read_plain_json_file(path) if path.exists() else {}
    except (RuntimeError, OSError) as exc:
        raise CellResourceError("trusted migration source archive cannot be read") from exc
    if (saved.get("database_identity") != catalog["database_identity"]
            or saved.get("database_volume") != backend.project_postgres_volume):
        return {}
    sources = saved.get("sources", {})
    if not isinstance(sources, dict) or any(not isinstance(v, str) for v in sources.values()):
        raise CellResourceError("trusted migration source archive is invalid")
    return dict(sources)


def _save(backend: Any, catalog: Mapping[str, Any], sources: Mapping[str, str]) -> None:
    write_controller_json(backend.metadata_path.parent / "migration-sources.json", {
        "database_identity": catalog["database_identity"],
        "database_volume": backend.project_postgres_volume,
        "sources": dict(sources),
    })


def _protected(catalog: Mapping[str, Any], archived: Mapping[str, str],
               files: Mapping[str, str]) -> dict[str, str]:
    protected = {}
    for path, digest in catalog["applied"].items():
        for candidate in (archived.get(path), files.get(path)):
            if (candidate is not None
                    and hashlib.sha256(candidate.encode("utf-8")).hexdigest() == digest):
                protected[path] = candidate
                break
        else:
            raise CellResourceError(
                f"applied migration {path} has no trusted source matching its database checksum; "
                "restore the exact applied SQL from trusted history; do not rewrite the journal"
            )
    return protected


def protected_sources(
    backend: Any, files: Mapping[str, str], *, verify_only: bool = False,
) -> dict[str, str]:
    # Restoration adaptation verifies a copied DB without classifying old SQL.
    # Its absent journal proves nothing; an actual journal remains authoritative.
    catalog = _catalog(backend, verify_only=verify_only)
    archived = _archive(backend, catalog)
    protected = _protected(catalog, archived, files)
    # Keep an in-flight intent available for recovery; only journal entries
    # become immutable, irrespective of what was saved before submission.
    _save(backend, catalog, {**archived, **protected})
    return protected


def require_protected_sources(protected: Mapping[str, str], files: Mapping[str, str]) -> None:
    for path, source in protected.items():
        if files.get(path) != source:
            raise CellResourceError(
                f"applied migration {path} cannot be changed or deleted; "
                "preserve its exact SQL and add a new migration for schema changes"
            )


def save_migration_intent(backend: Any, migrations: Mapping[str, str]) -> None:
    catalog = _catalog(backend)
    protected = _protected(catalog, _archive(backend, catalog), migrations)
    require_protected_sources(protected, migrations)
    # This durable write precedes SQL submission. A crash after COMMIT is recovered
    # by matching these exact UTF-8 bytes to the live journal on the next request.
    _save(backend, catalog, {**protected, **migrations})
