"""Row inventory that does not depend on schema analysis (AV03).

Counts are read-only observations: which relations exist and how many rows each
holds. Unsupported schema objects (a trigger, a custom default) never erase an
already observed "clients: 4 rows". Failures and timeouts are ``not_measured``,
never zero; ``empty`` requires full coverage of every business object.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from omnia_orchestrator.services.versioning.contracts import (
    Coverage,
    InventoryObject,
    InventoryReport,
    Presence,
)

RunSql = Callable[[str], bytes]

# Exact counts up to this bound; beyond it the table is "present" with an estimate.
EXACT_COUNT_LIMIT = 100_000
# Per-relation fallback is bounded: it runs under the workspace lock.
FALLBACK_LIMIT = 40
# Session guard for every counting script: read-only and time-bounded.
_GUARD = "SET statement_timeout = '5s';\nSET default_transaction_read_only = on;\n"

LIST_RELATIONS_SQL = _GUARD + """
SELECT json_build_object(
 'relations', coalesce((SELECT json_agg(json_build_object(
    'schema', n.nspname, 'name', c.relname, 'kind', c.relkind,
    'partition', c.relispartition, 'estimate', greatest(c.reltuples, 0)::bigint)
    ORDER BY n.nspname, c.relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname !~ '^pg_(toast|temp)'
   AND c.relkind IN ('r', 'p', 'v', 'm', 'f')), '[]'),
 'large_objects', EXISTS(SELECT FROM pg_largeobject_metadata));
"""

_KINDS = {
    "r": "table",
    "p": "partitioned_table",
    "v": "view",
    "m": "materialized_view",
    "f": "foreign_table",
}
# Migration bookkeeping, not business rows. Classified by exact identity only:
# a name prefix (omnia_*, tmp_*) is never a reason to ignore data.
_TECHNICAL = {("drizzle", "__drizzle_migrations"), ("public", "__drizzle_migrations")}


def quote_ident(value: str) -> str:
    """PostgreSQL identifier quoting that also covers unusual names."""
    if "\x00" in value or not value:
        raise ValueError("invalid SQL identifier")
    return '"' + value.replace('"', '""') + '"'


def _count_expression(schema: str, name: str) -> str:
    return (
        f"(SELECT count(*) FROM (SELECT 1 FROM {quote_ident(schema)}.{quote_ident(name)} "
        f"LIMIT {EXACT_COUNT_LIMIT + 1}) bounded)"
    )


def _measured(relation: dict[str, Any]) -> bool:
    # Partition leaves are counted through their root (no double counting);
    # views may execute arbitrary functions and foreign tables reach external
    # systems, so neither is scanned to establish presence.
    # Materialized views are derived (and may be unpopulated): not scanned either.
    return relation["kind"] in {"r", "p"} and not relation["partition"]


def _object(relation: dict[str, Any], count: int | None, diagnostic: str | None) -> InventoryObject:
    identity = (relation["schema"], relation["name"])
    kind = _KINDS[relation["kind"]]
    if identity in _TECHNICAL:
        classification = "technical"
    elif relation["kind"] in {"v", "m"}:
        classification = "derived"
    elif relation["kind"] == "f":
        classification = "unknown"
    else:
        classification = "business"
    label = f"{relation['schema']}.{relation['name']}"
    if count is None:
        return InventoryObject(
            object=label, kind=kind, classification=classification, presence="unknown",
            count_kind="not_measured", diagnostic=diagnostic,
        )
    if count > EXACT_COUNT_LIMIT:
        return InventoryObject(
            object=label, kind=kind, classification=classification, presence="present",
            row_count=max(int(relation.get("estimate") or 0), EXACT_COUNT_LIMIT + 1),
            count_kind="estimate",
        )
    return InventoryObject(
        object=label, kind=kind, classification=classification,
        presence="present" if count else "empty", row_count=count, count_kind="exact",
    )


def _counts(
    run_sql: RunSql, relations: list[dict[str, Any]]
) -> list[tuple[int | None, str | None]]:
    if not relations:
        return []
    expressions = ", ".join(_count_expression(r["schema"], r["name"]) for r in relations)
    try:
        values = json.loads(run_sql(_GUARD + f"SELECT json_build_array({expressions});"))
        if isinstance(values, list) and len(values) == len(relations):
            return [(int(value), None) for value in values]
    except Exception:
        pass
    # Fall back to one bounded statement per relation so a single timeout or
    # permission error only marks that relation as not measured.
    result: list[tuple[int | None, str | None]] = []
    for position, relation in enumerate(relations):
        if position >= FALLBACK_LIMIT:
            result.append((None, "count_skipped"))
            continue
        try:
            expression = _count_expression(relation["schema"], relation["name"])
            raw = run_sql(_GUARD + "SELECT " + expression + ";")
            result.append((int(raw.strip()), None))
        except Exception:
            result.append((None, "count_failed"))
    return result


def aggregate(objects: list[InventoryObject]) -> tuple[Presence, Coverage]:
    relevant = [o for o in objects if o.classification in {"business", "unknown"}]
    missing = [o for o in relevant if o.count_kind == "not_measured"]
    coverage: Coverage = "partial" if missing else "complete"
    if any(o.presence == "present" for o in relevant):
        return "present", coverage
    if not missing and all(o.presence == "empty" for o in relevant):
        return "empty", coverage
    return "unknown", coverage


def observe_inventory(
    run_sql: RunSql,
    *,
    observed_on: Literal["source", "candidate_copy"],
    schema_analysis: Coverage = "unavailable",
) -> InventoryReport:
    try:
        payload = json.loads(run_sql(LIST_RELATIONS_SQL))
        relations = payload["relations"]
        if not isinstance(relations, list) or len(relations) > 450:
            raise ValueError("relation list out of bounds")
    except Exception:
        return InventoryReport(
            presence="unknown", coverage="unavailable", schema_analysis=schema_analysis,
            observed_on=observed_on,
        )
    measured = [r for r in relations if _measured(r)]
    counted = dict(zip(
        [(r["schema"], r["name"]) for r in measured], _counts(run_sql, measured), strict=True
    ))
    objects: list[InventoryObject] = []
    for relation in relations:
        if relation["partition"]:
            continue
        count, diagnostic = counted.get((relation["schema"], relation["name"]), (None, None))
        objects.append(_object(relation, count, diagnostic or (
            None if _measured(relation) else "not_scanned_by_policy"
        )))
    if payload.get("large_objects"):
        objects.append(InventoryObject(
            object="pg_largeobject", kind="large_objects", classification="unknown",
            presence="present", count_kind="not_measured", diagnostic="large_objects_exist",
        ))
    presence, coverage = aggregate(objects)
    return InventoryReport(
        presence=presence, coverage=coverage, schema_analysis=schema_analysis,
        objects=objects, observed_on=observed_on,
    )
