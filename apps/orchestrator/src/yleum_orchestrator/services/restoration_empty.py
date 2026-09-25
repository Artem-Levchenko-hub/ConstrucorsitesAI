"""Fail-closed proof that a project database has no product records.

The proof is intentionally separate from the bounded UI inventory.  It uses one
REPEATABLE READ snapshot, exact counts and controller-superuser RLS bypass.  Any
stored object or PostgreSQL feature we cannot copy exactly makes the result
unknown instead of empty.
"""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yleum_orchestrator.services.restoration_binding import canonical_digest
from yleum_orchestrator.services.restoration_database import admin_sql

ObservationKind = Literal["source", "candidate_copy", "quiesced_source"]
_LEDGER_IDENTITY = ("public", "__omnia_migrations")
_MAX_USERS_IDENTITY = ("public", "max_users")
_MAX_USERS_COLUMNS = [
    ["id", "uuid", True, "gen_random_uuid()"],
    ["max_user_id", "text", True, None],
    ["first_name", "text", True, None],
    ["last_name", "text", False, None],
    ["username", "text", False, None],
    ["language_code", "text", False, None],
    ["photo_url", "text", False, None],
    ["created_at", "timestamp with time zone", True, "now()"],
    ["updated_at", "timestamp with time zone", True, "now()"],
]


class EmptyDatabaseWitness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    project_id: UUID
    workspace_id: UUID
    operation_id: UUID
    database_identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    objects_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    technical_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_rows_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_relations: list[Literal["public.__omnia_migrations", "public.max_users"]]
    observation_kind: ObservationKind
    all_business_empty: Literal[True] = True
    classification_policy: Literal["business_data_empty_v1"] = "business_data_empty_v1"

    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))

    def source_state(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value.pop("observation_kind")
        return value


# query_to_xml supplies safe dynamic relation reads inside this one server-side
# snapshot. Identifiers come from pg_catalog and are quoted by format(%I).
EMPTY_WITNESS_SQL = r"""
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';
SET LOCAL row_security = off;
WITH user_relations AS (
 SELECT n.nspname AS schema_name, c.relname, c.relkind, c.relispartition,
   c.relrowsecurity, pg_catalog.pg_get_userbyid(c.relowner)=current_user AS owner_is_controller,
   (SELECT coalesce(json_agg(json_build_array(a.attname,
      pg_catalog.format_type(a.atttypid,a.atttypmod), a.attnotnull,
      pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum), '[]')
    FROM pg_catalog.pg_attribute a
    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
    WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
   (SELECT coalesce(json_agg(a.attname ORDER BY u.ord), '[]')
    FROM pg_catalog.pg_constraint k
    CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum,ord)
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=u.attnum
    WHERE k.conrelid=c.oid AND k.contype='p') AS primary_key,
   (SELECT coalesce(json_agg(keys.names ORDER BY keys.names::text), '[]') FROM (
    SELECT json_agg(a.attname ORDER BY u.ord) AS names
    FROM pg_catalog.pg_index i
    CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY u(attnum,ord)
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=u.attnum
    WHERE i.indrelid=c.oid AND i.indisunique AND NOT i.indisprimary
     AND i.indexprs IS NULL AND i.indpred IS NULL AND u.ord<=i.indnkeyatts
    GROUP BY i.indexrelid) keys) AS unique_keys,
   CASE WHEN c.relkind = 'r' AND NOT c.relispartition THEN
    ((xpath('/table/row/n/text()', query_to_xml(
      format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname),
      false, false, '')))[1]::text)::bigint
   END AS row_count
 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname <> 'pg_toast'
  AND NOT pg_catalog.pg_is_other_temp_schema(n.oid)
  AND c.relkind IN ('r','p','v','m','f')
), user_sequences AS (
 SELECT n.nspname AS schema_name, c.relname,
   s.seqstart AS start_value,
   ((xpath('/table/row/last_value/text()', query_to_xml(
     format('SELECT last_value, is_called FROM %I.%I', n.nspname, c.relname),
     false, false, '')))[1]::text)::bigint AS last_value,
   ((xpath('/table/row/is_called/text()', query_to_xml(
     format('SELECT last_value, is_called FROM %I.%I', n.nspname, c.relname),
     false, false, '')))[1]::text)::boolean AS is_called
 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 JOIN pg_catalog.pg_sequence s ON s.seqrelid = c.oid
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname <> 'pg_toast'
  AND NOT pg_catalog.pg_is_other_temp_schema(n.oid)
)
SELECT json_build_object(
 'superuser', (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user),
 'row_security_off', current_setting('row_security') = 'off',
 'active_sessions', (SELECT count(*) FROM pg_catalog.pg_stat_activity
   WHERE datid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
    AND pid<>pg_backend_pid() AND backend_type='client backend'),
 'large_objects', EXISTS(SELECT FROM pg_catalog.pg_largeobject_metadata),
 'prepared_transactions', EXISTS(SELECT FROM pg_catalog.pg_prepared_xacts
   WHERE database=current_database()),
 'subscriptions', EXISTS(SELECT FROM pg_catalog.pg_subscription),
 'replication_slots', EXISTS(SELECT FROM pg_catalog.pg_replication_slots),
 'database_layout_valid', (SELECT count(*)=3
   AND bool_and(CASE datname WHEN 'postgres' THEN NOT datistemplate AND datallowconn
    WHEN 'template0' THEN datistemplate AND NOT datallowconn
    WHEN 'template1' THEN datistemplate AND datallowconn ELSE false END)
   FROM pg_catalog.pg_database),
 'extensions', coalesce((SELECT json_agg(extname ORDER BY extname)
   FROM pg_catalog.pg_extension), '[]'),
 'relations', coalesce((SELECT json_agg(json_build_object(
   'schema', schema_name, 'name', relname,
   'kind', CASE relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned_table'
     WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' ELSE 'foreign_table' END,
   'partition', relispartition, 'rls', relrowsecurity, 'row_count', row_count,
   'ledger_attestation', CASE WHEN schema_name='public' AND relname='__omnia_migrations'
    THEN json_build_object('shape_attested', owner_is_controller
     AND columns::jsonb = '[
       ["name","text",true,null],
       ["applied_at","timestamp with time zone",true,"now()"]
     ]'::jsonb
     AND primary_key::jsonb = '["name"]'::jsonb AND unique_keys::jsonb = '[]'::jsonb,
     'rows_digest',((xpath('/table/row/digest/text()', query_to_xml(
       $ledger$SELECT encode(sha256(convert_to(coalesce(jsonb_agg(
        jsonb_build_array(name,applied_at) ORDER BY name)::text,'[]'),'UTF8')),'hex') AS digest
        FROM public.__omnia_migrations$ledger$, false, false, '')))[1]::text))
    ELSE NULL END,
   'max_users_attestation', CASE WHEN schema_name='public' AND relname='max_users'
    THEN json_build_object('owner_is_controller',owner_is_controller,
      'columns',columns,'primary_key',primary_key,'unique_keys',unique_keys,
      'rows_digest',((xpath('/table/row/digest/text()', query_to_xml(
       $users$SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(t)
        ORDER BY max_user_id)::text,'[]'),'UTF8')),'hex') AS digest FROM public.max_users t$users$,
       false, false, '')))[1]::text)) ELSE NULL END)
   ORDER BY schema_name, relname) FROM user_relations), '[]'),
 'sequences', coalesce((SELECT json_agg(json_build_object(
   'schema', schema_name, 'name', relname, 'start_value', start_value,
   'last_value', last_value, 'is_called', is_called)
   ORDER BY schema_name, relname) FROM user_sequences), '[]')
);
COMMIT;
"""


TEMPLATE1_WITNESS_SQL = r"""
\connect template1
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';
SET LOCAL row_security = off;
SELECT json_build_object(
 'database', current_database(),
 'superuser', (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user),
 'row_security_off', current_setting('row_security') = 'off',
 'active_sessions', (SELECT count(*) FROM pg_catalog.pg_stat_activity
   WHERE datid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
    AND pid<>pg_backend_pid() AND backend_type='client backend'),
 'large_objects', EXISTS(SELECT FROM pg_catalog.pg_largeobject_metadata),
 'prepared_transactions', EXISTS(SELECT FROM pg_catalog.pg_prepared_xacts
   WHERE database=current_database()),
 'extensions', coalesce((SELECT json_agg(extname ORDER BY extname)
   FROM pg_catalog.pg_extension), '[]'),
 'schemas', coalesce((SELECT json_agg(n.nspname ORDER BY n.nspname)
   FROM pg_catalog.pg_namespace n
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname <> 'pg_toast'
    AND NOT pg_catalog.pg_is_other_temp_schema(n.oid)), '[]'),
 'relations', (SELECT count(*) FROM pg_catalog.pg_class c
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname <> 'pg_toast'
    AND NOT pg_catalog.pg_is_other_temp_schema(n.oid)),
 'routines', (SELECT count(*) FROM pg_catalog.pg_proc p
   JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname <> 'pg_toast'
    AND NOT pg_catalog.pg_is_other_temp_schema(n.oid)),
 'types', (SELECT count(*) FROM pg_catalog.pg_type t
   JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname <> 'pg_toast'
    AND NOT pg_catalog.pg_is_other_temp_schema(n.oid)),
 'default_privileges', (SELECT count(*) FROM pg_catalog.pg_default_acl)
);
COMMIT;
"""


def _classified_relations(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    relations = payload.get("relations")
    if not isinstance(relations, list) or len(relations) > 450:
        return None
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in relations:
        if not isinstance(raw, dict):
            return None
        schema, name, kind = raw.get("schema"), raw.get("name"), raw.get("kind")
        if (
            not isinstance(schema, str)
            or not isinstance(name, str)
            or kind != "table"
            or raw.get("partition") is True
            or type(raw.get("row_count")) is not int
            or raw["row_count"] < 0
        ):
            return None
        identity = (schema, name)
        if identity in seen:
            return None
        seen.add(identity)
        classification = "business"
        identity_rows_digest = None
        if identity == _LEDGER_IDENTITY:
            attestation = raw.get("ledger_attestation")
            rows_digest = attestation.get("rows_digest") if isinstance(attestation, dict) else None
            canonical = bool(
                isinstance(attestation, dict)
                and attestation.get("shape_attested") is True
                and isinstance(rows_digest, str)
                and len(rows_digest) == 64
            )
            if raw.get("row_count") and not canonical:
                return None
            if canonical:
                classification = "technical"
                identity_rows_digest = rows_digest
        elif identity == _MAX_USERS_IDENTITY:
            attestation = raw.get("max_users_attestation")
            rows_digest = attestation.get("rows_digest") if isinstance(attestation, dict) else None
            canonical = bool(
                isinstance(attestation, dict)
                and attestation.get("owner_is_controller") is True
                and attestation.get("columns") == _MAX_USERS_COLUMNS
                and attestation.get("primary_key") == ["id"]
                and attestation.get("unique_keys") == [["max_user_id"]]
                and isinstance(rows_digest, str)
                and len(rows_digest) == 64
            )
            if raw.get("row_count") and not canonical:
                return None
            if canonical:
                classification = "technical"
                identity_rows_digest = rows_digest
        result.append(
            {
                "schema": schema,
                "name": name,
                "kind": kind,
                "classification": classification,
                "row_count": raw["row_count"],
                "rls": raw.get("rls") is True,
                "identity_rows_digest": identity_rows_digest,
            }
        )
    return sorted(result, key=lambda item: (item["schema"], item["name"]))


def _stock_template1(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    expected = {
        "database": "template1",
        "superuser": True,
        "row_security_off": True,
        "active_sessions": 0,
        "large_objects": False,
        "prepared_transactions": False,
        "extensions": ["plpgsql"],
        "schemas": ["public"],
        "relations": 0,
        "routines": 0,
        "types": 0,
        "default_privileges": 0,
    }
    return expected if payload == expected else None


def observe_empty_database(
    backend: Any,
    *,
    operation_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    database_identity_digest: str,
    observation_kind: ObservationKind,
) -> EmptyDatabaseWitness | None:
    """Return proof only for a completely measured, zero-business database."""
    try:
        payload = json.loads(admin_sql(backend, EMPTY_WITNESS_SQL, max_bytes=512 * 1024))
        template1 = _stock_template1(
            json.loads(admin_sql(backend, TEMPLATE1_WITNESS_SQL, max_bytes=64 * 1024))
        )
    except Exception:
        return None
    if not isinstance(payload, dict) or template1 is None:
        return None
    if payload.get("superuser") is not True or payload.get("row_security_off") is not True:
        return None
    if any(
        payload.get(name) is not False
        for name in (
            "large_objects",
            "prepared_transactions",
            "subscriptions",
            "replication_slots",
        )
    ):
        return None
    if (
        observation_kind in {"candidate_copy", "quiesced_source"}
        and payload.get("active_sessions") != 0
    ):
        return None
    if payload.get("database_layout_valid") is not True:
        return None
    extensions = payload.get("extensions")
    if not isinstance(extensions, list) or any(name != "plpgsql" for name in extensions):
        return None
    sequences = payload.get("sequences")
    if not isinstance(sequences, list) or any(
        not isinstance(item, dict)
        or item.get("is_called") is not False
        or type(item.get("last_value")) is not int
        or type(item.get("start_value", item.get("last_value"))) is not int
        or item.get("last_value") != item.get("start_value", item.get("last_value"))
        for item in sequences
    ):
        return None
    objects = _classified_relations(payload)
    if objects is None or any(
        item["classification"] != "technical" and item["row_count"] != 0 for item in objects
    ):
        return None
    catalog = [
        {key: item[key] for key in ("schema", "name", "kind", "classification", "rls")}
        for item in objects
    ]
    technical = [item for item in objects if item["classification"] == "technical"]
    identity_digests = [
        {"object": item["schema"] + "." + item["name"], "digest": item["identity_rows_digest"]}
        for item in technical
        if item["identity_rows_digest"]
    ]
    identity_rows_digest = canonical_digest(identity_digests)
    return EmptyDatabaseWitness(
        project_id=project_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        database_identity_digest=database_identity_digest,
        catalog_digest=canonical_digest(catalog),
        objects_digest=canonical_digest(objects),
        technical_state_digest=canonical_digest(
            {
                "relations": technical,
                "sequences": sequences,
                "extensions": extensions,
                "template1": template1,
            }
        ),
        identity_rows_digest=identity_rows_digest,
        identity_relations=[item["object"] for item in identity_digests],
        observation_kind=observation_kind,
    )


def same_empty_source(prepared: EmptyDatabaseWitness, current: EmptyDatabaseWitness) -> bool:
    return prepared.source_state() == current.source_state()
