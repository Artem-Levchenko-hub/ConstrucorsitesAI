"""Guard administrator serialization before any project value is evaluated.

Name qualification cannot stop PostgreSQL dispatching a cast/output function by
OID. Keep definition catalogs stable, then inspect metadata under READ ONLY.
Ordinary enums and their native arrays need no project cast and remain supported.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

# These SHARE locks exclude definition changes, including CREATE/DROP CAST and
# replacement of output functions. A snapshot or workspace advisory lock alone
# would not prevent OID-based dispatch observing concurrent catalog changes.
_DEFINITION_CATALOGS = (
    "pg_attribute",
    "pg_cast",
    "pg_class",
    "pg_namespace",
    "pg_proc",
    "pg_type",
)

UNSUPPORTED_CONVERSIONS_SQL = """
WITH RECURSIVE value_types(oid) AS (
 SELECT a.atttypid FROM pg_catalog.pg_attribute a
 JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r','p')
  AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
 UNION
 SELECT t.typelem FROM pg_catalog.pg_type t JOIN value_types v ON v.oid = t.oid
 WHERE t.typelem <> 0
)
SELECT EXISTS (
 SELECT FROM pg_catalog.pg_cast c
 JOIN pg_catalog.pg_type s ON s.oid = c.castsource
 JOIN pg_catalog.pg_type t ON t.oid = c.casttarget
 LEFT JOIN pg_catalog.pg_proc f ON f.oid = c.castfunc
 WHERE s.typnamespace <> 'pg_catalog'::pg_catalog.regnamespace
  OR t.typnamespace <> 'pg_catalog'::pg_catalog.regnamespace
  OR (c.castfunc <> 0 AND f.pronamespace IS DISTINCT FROM
      'pg_catalog'::pg_catalog.regnamespace)
) OR EXISTS (
 SELECT FROM value_types v
 LEFT JOIN pg_catalog.pg_type t ON t.oid = v.oid
 LEFT JOIN pg_catalog.pg_proc f ON f.oid = t.typoutput
 WHERE t.oid IS NULL OR f.pronamespace IS DISTINCT FROM 'pg_catalog'::pg_catalog.regnamespace
  OR NOT (
   t.typnamespace = 'pg_catalog'::pg_catalog.regnamespace
   OR (t.typtype = 'e' AND t.typoutput = 'pg_catalog.enum_out'::pg_catalog.regproc)
   OR (t.typtype = 'b' AND t.typelem <> 0
       AND t.typoutput = 'pg_catalog.array_out'::pg_catalog.regproc
       AND t.typsubscript = 'pg_catalog.array_subscript_handler'::pg_catalog.regproc)
  )
)
"""


def row_conversion_preamble(
    relations: Sequence[tuple[str, str]],
    *,
    timeout_seconds: int = 60,
    allow_missing: bool = False,
) -> str:
    """Return a transaction preamble; caller appends its SELECT and COMMIT.

    Catalog-wide custom casts are deliberately unsupported even when unrelated
    to the selected table. No project callback is evaluated to decide admission.
    The brief READ WRITE phase acquires only locks, never reads project values or
    writes data. PostgreSQL permits READ WRITE -> READ ONLY while retaining locks.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ValueError("invalid row observation timeout")
    targets = []
    for schema, name in relations:
        if any(
            not isinstance(value, str) or not value or "\x00" in value for value in (schema, name)
        ):
            raise ValueError("invalid row observation relation")
        targets.append({"schema_name": schema, "relation_name": name})
    encoded = json.dumps(targets, separators=(",", ":")).encode().hex()
    missing = "false" if allow_missing else "true"
    catalogs = ", ".join(f"pg_catalog.{name}" for name in _DEFINITION_CATALOGS)
    return (
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ WRITE;\n"
        "SET LOCAL search_path = pg_catalog;\n"
        f"SET LOCAL statement_timeout = '{timeout_seconds}s';\n"
        "SET LOCAL lock_timeout = '5s';\n"
        f"LOCK TABLE {catalogs} IN SHARE MODE;\n"
        "SET TRANSACTION READ ONLY;\n"
        f"""DO $row_conversion_guard$
BEGIN
 IF ({UNSUPPORTED_CONVERSIONS_SQL}) THEN
  RAISE EXCEPTION USING ERRCODE = '0A000',
   MESSAGE = 'project value conversion is unsupported for administrator observations';
 END IF;
 IF EXISTS (
  SELECT FROM pg_catalog.jsonb_to_recordset(pg_catalog.convert_from(
   pg_catalog.decode('{encoded}', 'hex'), 'UTF8')::pg_catalog.jsonb)
   AS requested(schema_name pg_catalog.text, relation_name pg_catalog.text)
  LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = requested.schema_name
  LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid
    AND c.relname = requested.relation_name
  WHERE (c.oid IS NULL AND {missing}) OR c.relkind NOT IN ('r','p')
 ) THEN
  RAISE EXCEPTION USING ERRCODE = '0A000',
   MESSAGE = 'row observation requires a stored table';
 END IF;
END
$row_conversion_guard$;\n"""
    )
