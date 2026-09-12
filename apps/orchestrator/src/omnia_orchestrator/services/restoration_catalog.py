"""Read schema structure, never row contents, for a supported restore candidate."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from omnia_orchestrator.schemas.code_restoration import RestorationDatabaseState
from omnia_orchestrator.services.restoration_data_contract import (
    DataContract,
    _json_guard,
    json_guard_function_name,
)
from omnia_orchestrator.services.restoration_database import admin_sql

# Observe the isolated imported database, not the filtered ownership contract.
# Only the controller signing table is excluded, never an application-name prefix.
DATABASE_INVENTORY_SQL = """
SELECT json_build_object(
 'unsupported', EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname !~ '^pg_(toast|temp)_?'
    AND NOT (n.nspname='omnia_guard' AND c.relname='identity')
    AND (c.relkind IN ('v','m','p','f') OR
      (c.relkind='r' AND (n.nspname<>'public' OR
       EXISTS(SELECT FROM pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)))))
   OR EXISTS(SELECT FROM pg_largeobject_metadata),
 'tables', coalesce((SELECT json_agg(c.relname ORDER BY c.relname)
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r'), '[]'));
"""


def database_presence_query(payload: dict[str, Any]) -> str | None:
    """Return one snapshot's existence query; unknown inventories never mean empty."""
    from omnia_orchestrator.services.restoration_data_contract import qi

    names = payload.get("tables")
    if payload.get("unsupported") is not False or not isinstance(names, list) or len(names) > 200:
        return None
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        return None
    predicates = [f"EXISTS(SELECT 1 FROM public.{qi(name)})" for name in names]
    return "SELECT " + (" OR ".join(predicates) if predicates else "false") + ";"


def database_state(backend: Any) -> RestorationDatabaseState:
    """Informational row presence in an isolated copy, never permission to reset data."""
    try:
        payload = json.loads(admin_sql(backend, DATABASE_INVENTORY_SQL, max_bytes=64 * 1024))
        if not isinstance(payload, dict):
            return "unknown"
        query = database_presence_query(payload)
        if query is None:
            return "unknown"
        observed = admin_sql(backend, query, max_bytes=32).strip()
        if observed == b"t":
            return "present"
        return "empty" if observed == b"f" else "unknown"
    except Exception:
        # A failed probe is not evidence of no data. Do not expose SQL errors/values.
        return "unknown"


CATALOG_SQL = """
SELECT json_build_object('event_triggers', EXISTS(
 SELECT FROM pg_event_trigger WHERE evtenabled <> 'D'),
 'unsupported_relations', EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND (c.relkind IN ('v','m','p','f')
     OR EXISTS(SELECT FROM pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid))),
 'tables', coalesce(json_agg(t), '[]')) FROM (
 SELECT c.relname AS name,
 (SELECT json_agg(json_build_object('name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
    'nullable',NOT a.attnotnull, 'values', (SELECT json_agg(e.enumlabel ORDER BY e.enumsortorder)
      FROM pg_enum e WHERE e.enumtypid=a.atttypid)) ORDER BY a.attnum)
  FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
 (SELECT coalesce(json_agg(pg_get_constraintdef(k.oid) ORDER BY k.conname),'[]')
  FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='c') AS checks,
 (SELECT coalesce(json_agg(json_build_object(
   'column',a.attname,'table',p.relname,'target',b.attname,'on_delete',
   CASE k.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL'
    WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END, 'on_update',
   CASE k.confupdtype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL'
    WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END)
   ORDER BY k.conname),'[]')
  FROM pg_constraint k JOIN pg_class p ON p.oid=k.confrelid
   JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.conkey[1]
   JOIN pg_attribute b ON b.attrelid=p.oid AND b.attnum=k.confkey[1]
  WHERE k.conrelid=c.oid AND k.contype='f') AS foreign_keys,
 (SELECT coalesce(json_agg(json_build_object('name',tr.tgname,'function',p.proname,
   'schema',n.nspname,'owner',p.proowner::regrole::text,'source',p.prosrc,
   'security_definer',p.prosecdef,'language',l.lanname,'config',p.proconfig,
   'return_type',p.prorettype::regtype::text,'enabled',tr.tgenabled,'type',tr.tgtype,
   'arguments',tr.tgnargs,'predicate',tr.tgqual IS NOT NULL,'columns',tr.tgattr::text)), '[]')
 FROM pg_trigger tr JOIN pg_proc p ON p.oid=tr.tgfoid
 JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
 WHERE tr.tgrelid=c.oid AND NOT tr.tgisinternal) AS triggers,
 (SELECT coalesce(json_agg(a.attname ORDER BY u.ord),'[]') FROM pg_constraint k
 CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum,ord)
 JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=u.attnum
 WHERE k.conrelid=c.oid AND k.contype='p') AS primary_key,
 (SELECT coalesce(json_agg(keys.names ORDER BY keys.names::text),'[]') FROM (
  SELECT json_agg(a.attname ORDER BY u.ord) AS names FROM pg_index i
  CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY u(attnum,ord)
  JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=u.attnum
  WHERE i.indrelid=c.oid AND i.indisunique AND NOT i.indisprimary
   AND i.indexprs IS NULL AND i.indpred IS NULL AND u.ord<=i.indnkeyatts
  GROUP BY i.indexrelid) keys) AS unique_keys,
 EXISTS(SELECT FROM pg_index i WHERE i.indrelid=c.oid AND
   (i.indexprs IS NOT NULL OR i.indpred IS NOT NULL OR NOT i.indisvalid
    OR i.indnullsnotdistinct)) AS custom_indexes,
 EXISTS(SELECT FROM pg_constraint k WHERE k.conrelid=c.oid AND
   (k.contype NOT IN ('p','u','f') OR k.condeferrable OR NOT k.convalidated)) AS custom_constraints,
 EXISTS(SELECT FROM pg_attribute a JOIN pg_type ty ON ty.oid=a.atttypid
   WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
    AND (a.attgenerated<>'' OR ty.typtype IN ('d','c','r','m')
      OR (ty.typtype='b' AND ty.typnamespace<>'pg_catalog'::regnamespace)))
 OR EXISTS(SELECT FROM pg_rewrite r WHERE r.ev_class=c.oid)
 OR EXISTS(SELECT FROM pg_attrdef d WHERE d.adrelid=c.oid AND
   pg_get_expr(d.adbin,d.adrelid) NOT IN ('now()','CURRENT_TIMESTAMP','gen_random_uuid()',
     'true','false','NULL::text') AND pg_get_expr(d.adbin,d.adrelid) !~ '^-?[0-9]+(\\.[0-9]+)?$')
 AS custom_column_behavior,
 EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid
   AND k.contype='f' AND array_length(k.conkey,1) <> 1) AS composite_foreign_keys
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r' AND c.relname NOT LIKE 'omnia_%'
 ORDER BY c.relname
) t;
"""


def infer_ownership(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only conventional, explicit actor columns or a directly owned parent."""
    for table in tables:
        names = {column["name"] for column in table["columns"]}
        owner = next(
            (
                name
                for name in (
                    "owner_max_user_id",
                    "max_user_id",
                    "owner_id",
                    "user_id",
                )
                if name in names
            ),
            None,
        )
        if owner:
            table["owner_column"] = owner
    parents = {table["name"]: table for table in tables if table.get("owner_column")}
    for table in tables:
        if table.get("owner_column"):
            continue
        refs = [ref for ref in table.get("foreign_keys", []) if ref["table"] in parents]
        if len(refs) == 1:
            table["owner_reference"] = {key: refs[0][key] for key in ("column", "table", "target")}
        else:
            # Such a table may be a public catalogue. It is never automatically
            # given write rights without an explicit actor contract.
            raise ValueError("unknown actor ownership:" + table["name"])
    return tables


def catalog_contract(
    backend: Any,
    *,
    trusted_contract: DataContract | None = None,
) -> tuple[DataContract, list[str]]:
    contract, blockers = contract_from_catalog(
        json.loads(admin_sql(backend, CATALOG_SQL)), trusted_contract
    )
    from omnia_orchestrator.services.restoration_data_contract import qi, ql

    for table in contract.tables:
        for column in table.columns:
            if column.type not in {"json", "jsonb"} or column.json_keys is None:
                continue
            keys = "ARRAY[" + ",".join(ql(key) for key in column.json_keys) + "]::text[]"
            expression = qi(column.name) + "::jsonb"
            query = (
                f"SELECT EXISTS(SELECT FROM public.{qi(table.name)} WHERE "
                f"jsonb_typeof({expression}) <> 'object' OR EXISTS(SELECT FROM "
                f"jsonb_each(CASE WHEN jsonb_typeof({expression})='object' THEN {expression} "
                f"ELSE '{{}}'::jsonb END) p WHERE p.key=ANY({keys}) "
                "AND jsonb_typeof(p.value) IN ('object','array')))"
            )
            if admin_sql(backend, query).strip() != b"f":
                blockers.append(f"nested_json_requires_adaptation:{table.name}.{column.name}")
    return contract, blockers


def contract_from_catalog(
    payload: dict[str, Any],
    trusted_contract: DataContract | None = None,
) -> tuple[DataContract, list[str]]:
    tables = deepcopy(payload["tables"])
    blockers = ["enabled_event_triggers"] if payload["event_triggers"] else []
    if payload.get("unsupported_relations"):
        blockers.append("unsupported_relations")
    trusted = {table.name: table for table in trusted_contract.tables} if trusted_contract else {}
    for table in tables:
        approved = trusted.get(table["name"])
        triggers = table.pop("triggers", [])
        if any(not _trusted_json_trigger(table["name"], trigger, approved) for trigger in triggers):
            blockers.append("custom_triggers:" + table["name"])
        for flag in ("custom_indexes", "custom_constraints", "custom_column_behavior"):
            if table.pop(flag, False):
                blockers.append(flag + ":" + table["name"])
        if table.pop("composite_foreign_keys", False):
            blockers.append("composite_foreign_keys:" + table["name"])
        for column in table["columns"]:
            column["type"] = normalize_type(column["type"])
            declared = (
                next((item for item in approved.columns if item.name == column["name"]), None)
                if approved
                else None
            )
            if declared and normalize_type(declared.type) == column["type"]:
                column["meaning"], column["json_keys"] = declared.meaning, declared.json_keys
    try:
        owned = infer_ownership(tables)
    except ValueError as error:
        return DataContract(version=1), [*blockers, str(error)]
    from omnia_orchestrator.services.restoration_data_contract import foreign_key_cycle_nodes

    contract = DataContract(version=1, tables=owned)
    blockers.extend("foreign_key_cycle:" + name for name in foreign_key_cycle_nodes(contract))
    return contract, blockers


def _trusted_json_trigger(table: str, trigger: dict[str, Any], trusted: Any) -> bool:
    if trusted is None:
        return False
    for column in trusted.columns:
        if column.type not in {"json", "jsonb"} or column.json_keys is None:
            continue
        name = json_guard_function_name(table, column.name)
        expected = {
            "name": name,
            "function": name,
            "schema": "omnia_guard",
            "owner": "postgres",
            "source": _json_guard(table, column)[0].split("$json$")[1],
            "security_definer": True,
            "language": "plpgsql",
            "config": ["search_path=pg_catalog, omnia_guard"],
            "return_type": "trigger",
            "enabled": "O",
            "type": 23,
            "arguments": 0,
            "predicate": False,
            "columns": "",
        }
        if trigger == expected:
            return True
    return False


# Runs only inside the candidate with its own data and no public egress.
# The result is validated against the real catalog and never grants ownership.
DRIZZLE_CATALOG_JS = r"""
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('/workspace/node_modules/typescript');
Module._extensions['.ts'] = (module, filename) => {
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  module._compile(source, filename);
};
const {getTableConfig} = require('/workspace/node_modules/drizzle-orm/pg-core');
const {getTableName} = require('/workspace/node_modules/drizzle-orm');
const schema = require('/workspace/src/lib/db/schema.ts');
const tables = [];
for (const item of Object.values(schema)) {
  let config; try { config = getTableConfig(item); } catch { continue; }
  if (!config?.columns?.length) continue;
  if (config.schema && config.schema !== 'public') throw Error('Unsupported schema');
  if (config.checks.length) throw Error('Check expressions require adaptation');
  const primary = [...config.columns.filter(c => c.primary).map(c => c.name),
    ...config.primaryKeys.flatMap(k => k.columns.map(c => c.name))];
  const unique = config.columns.filter(c => c.isUnique).map(c => {
    if(c.uniqueType === 'not distinct') throw Error('Custom unique semantics');
    return [c.name];
  });
  for(const key of config.uniqueConstraints || []) {
    if(key.nullsNotDistinct) throw Error('Custom unique semantics');
    unique.push(key.columns.map(c => c.name));
  }
  for(const index of config.indexes) {
    const c = index.config;
    if(c.where || c.method && c.method !== 'btree' || c.columns.some(x => !x.name))
      throw Error('Custom index expressions require adaptation');
    if(c.unique) unique.push(c.columns.map(x => x.name));
  }
  unique.sort((a,b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  tables.push({name:config.name, columns:config.columns.map(column => ({
    name:column.name, type:column.getSQLType(), nullable:!column.notNull,
    ...(column.enumValues?.length ? {values:column.enumValues}:{}),
  })), primary_key:primary, unique_keys:unique, checks:[],
  foreign_keys:config.foreignKeys.map(fk => {
    const ref = fk.reference();
    if(ref.columns.length!==1) throw Error('Composite relation needs an explicit contract');
    return {column:ref.columns[0].name, table:getTableName(ref.foreignTable),
      target:ref.foreignColumns[0].name, on_delete:(fk.onDelete||'no action').toUpperCase(),
      on_update:(fk.onUpdate||'no action').toUpperCase()};
  })});
}
process.stdout.write(JSON.stringify(tables));
"""


def candidate_contract(backend: Any, files: dict[str, str]) -> DataContract:
    package = json.loads(files.get("package.json", "{}"))
    scripts = package.get("scripts", {})
    if any(key in scripts for key in ("prestart", "poststart", "predev", "postdev")):
        raise ValueError("historical startup hooks require adaptation")
    explicit = files.get(".omnia/data-contract.json")
    if explicit:
        contract = DataContract.model_validate_json(explicit)
        if any(table.checks for table in contract.tables):
            raise ValueError("historical check expressions require adaptation")
        data = contract.model_dump()
        for table in data["tables"]:
            for column in table["columns"]:
                column["type"] = normalize_type(column["type"])
        return DataContract.model_validate(data)
    if "src/lib/db/schema.ts" not in files:
        return DataContract(version=1)
    container = backend._container()
    outcome = container.exec_run(
        ["timeout", "30", "node", "-e", DRIZZLE_CATALOG_JS],
        workdir="/workspace",
    )
    if outcome.exit_code != 0 or len(outcome.output) > 1024 * 1024:
        raise ValueError("historical data contract requires adaptation")
    tables = json.loads(outcome.output)
    for table in tables:
        for column in table["columns"]:
            column["type"] = normalize_type(column["type"])
    return DataContract(version=1, tables=infer_ownership(tables))


def normalize_type(value: str) -> str:
    original = " ".join(value.strip().split())
    value = original.lower()
    aliases = {
        "int": "integer",
        "int4": "integer",
        "int2": "smallint",
        "int8": "bigint",
        "bool": "boolean",
        "timestamp": "timestamp without time zone",
        "timestamptz": "timestamp with time zone",
        "float4": "real",
        "float8": "double precision",
        "decimal": "numeric",
        "serial": "integer",
        "bigserial": "bigint",
        "smallserial": "smallint",
    }
    if value.endswith("[]"):
        return normalize_type(value[:-2]) + "[]"
    if re.fullmatch(r"varchar(?:\(\d+\))?", value):
        return value.replace("varchar", "character varying", 1)
    if value in aliases:
        return aliases[value]
    if value in {
        "uuid",
        "text",
        "boolean",
        "integer",
        "smallint",
        "bigint",
        "json",
        "jsonb",
        "date",
        "time",
        "real",
        "double precision",
        "numeric",
        "timestamp without time zone",
        "timestamp with time zone",
    }:
        return value
    return original
