"""Read schema structure, never row contents, for a supported restore candidate."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from omnia_orchestrator.schemas.code_restoration import RestorationDatabaseState
from omnia_orchestrator.services.restoration_data_contract import DataContract
from omnia_orchestrator.services.restoration_database import admin_sql

# Observe the isolated imported database, never an application-name prefix.
DATABASE_INVENTORY_SQL = """
SELECT json_build_object(
 'unsupported', EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    AND n.nspname !~ '^pg_(toast|temp)_?'
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
      FROM pg_enum e WHERE e.enumtypid=a.atttypid),
    'default', (SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
      WHERE d.adrelid=c.oid AND d.adnum=a.attnum AND a.attgenerated=''),
    'identity', CASE a.attidentity WHEN 'a' THEN 'always' WHEN 'd' THEN 'by_default' END)
    ORDER BY a.attnum)
  FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
 (SELECT coalesce(json_agg(json_build_object('name',k.conname,
    'definition',pg_get_constraintdef(k.oid)) ORDER BY k.conname),'[]')
  FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='c'
   AND NOT k.condeferrable AND k.convalidated) AS check_constraints,
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
 (SELECT coalesce(json_agg(ci.relname ORDER BY ci.relname),'[]') FROM pg_index i
   JOIN pg_class ci ON ci.oid=i.indexrelid WHERE i.indrelid=c.oid AND
   (i.indexprs IS NOT NULL OR i.indpred IS NOT NULL OR NOT i.indisvalid
    OR i.indnullsnotdistinct)) AS custom_indexes,
 (SELECT coalesce(json_agg(k.conname ORDER BY k.conname),'[]') FROM pg_constraint k
   WHERE k.conrelid=c.oid AND
   (k.contype NOT IN ('p','u','f','c') OR k.condeferrable OR NOT k.convalidated))
 AS custom_constraints,
 (SELECT coalesce(json_agg(x.name ORDER BY x.name),'[]') FROM (
  SELECT a.attname AS name FROM pg_attribute a JOIN pg_type ty ON ty.oid=a.atttypid
   WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
    AND (a.attgenerated<>'' OR ty.typtype IN ('d','c','r','m')
      OR (ty.typtype='b' AND ty.typnamespace<>'pg_catalog'::regnamespace))
  UNION SELECT '(rule:'||r.rulename||')' FROM pg_rewrite r WHERE r.ev_class=c.oid) x)
 AS custom_column_behavior,
 EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid
   AND k.contype='f' AND array_length(k.conkey,1) <> 1) AS composite_foreign_keys
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r' AND c.relname NOT LIKE 'omnia_%'
 ORDER BY c.relname
) t;
"""


def infer_ownership(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate conventional owner columns; tables without one stay ordinary tables."""
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
    return tables


# pg_catalog first: an unqualified now()/nextval() in the rendered catalog is then
# always the built-in, never a same-named function a role's search_path puts first.
CATALOG_SCRIPT = "SET search_path = pg_catalog, public;\n" + CATALOG_SQL


def catalog_contract(backend: Any) -> tuple[DataContract, list[str]]:
    return contract_from_catalog(json.loads(admin_sql(backend, CATALOG_SCRIPT)))


# Literal defaults (with an optional cast) and PostgreSQL's own id/time minting.
# Anything else, above all a call into a user-defined function, stays a blocker:
# a default executes on every historical insert.
_ORDINARY_DEFAULT = re.compile(
    r"^(?:'(?:[^']|'')*'(?:::[a-z_][a-z0-9_ ]*(?:\(\d+(?:,\d+)?\))?(?:\[\])?)?"
    r"|\(?-?\d+(?:\.\d+)?\)?(?:::[a-z_][a-z0-9_ ]*)?"
    r"|true|false|NULL(?:::[a-z_][a-z0-9_ ]*(?:\[\])?)?"
    r"|now\(\)|CURRENT_TIMESTAMP|CURRENT_DATE|LOCALTIMESTAMP|clock_timestamp\(\)"
    r"|statement_timestamp\(\)|transaction_timestamp\(\)|gen_random_uuid\(\)"
    r"|nextval\('[a-zA-Z0-9_.\"]+'::regclass\))$"
)


def ordinary_default(expression: str) -> bool:
    return bool(_ORDINARY_DEFAULT.match(expression.strip()))


def _named(value: Any) -> list[str]:
    """Catalog flags are object-name lists; older fixtures send booleans."""
    if isinstance(value, list):
        return [str(item) for item in value]
    return ["*"] if value else []


def contract_from_catalog(payload: dict[str, Any]) -> tuple[DataContract, list[str]]:
    contract, blockers, _ = describe_catalog(payload)
    return contract, blockers


def describe_catalog(
    payload: dict[str, Any],
) -> tuple[DataContract, list[str], list[dict[str, str]]]:
    """Contract + legacy blocker tokens + precise unsupported objects.

    Every object the analyzer cannot model is named, so the report says *which*
    trigger/default/index needs verification instead of a table-wide flag."""
    tables = deepcopy(payload["tables"])
    blockers = ["enabled_event_triggers"] if payload["event_triggers"] else []
    unsupported: list[dict[str, str]] = []
    if payload["event_triggers"]:
        unsupported.append({"kind": "event_trigger", "object": "database"})
    if payload.get("unsupported_relations"):
        blockers.append("unsupported_relations")
        unsupported.append({"kind": "relation", "object": "public"})
    for table in tables:
        name = table["name"]
        triggers = table.pop("triggers", [])
        if triggers:
            blockers.append("custom_triggers:" + name)
            unsupported.extend(
                {"kind": "trigger", "object": f"public.{name}.{trigger.get('name', '?')}"}
                for trigger in triggers
            )
        for flag, kind in (
            ("custom_indexes", "index"),
            ("custom_constraints", "constraint"),
            ("custom_column_behavior", "column_behavior"),
        ):
            objects = _named(table.pop(flag, False))
            if objects:
                blockers.append(flag + ":" + name)
                unsupported.extend(
                    {"kind": kind, "object": f"public.{name}" + ("" if item == "*" else f".{item}")}
                    for item in objects
                )
        custom_defaults = [
            column["name"]
            for column in table["columns"]
            if column.get("default")
            and (len(column["default"]) > 2000 or not ordinary_default(column["default"]))
        ]
        for column in table["columns"]:
            if column.get("default") and len(column["default"]) > 2000:
                column["default"] = None  # reported below as an unsupported default
        long_checks = [
            check for check in table.get("check_constraints", [])
            if len(check["definition"]) > 4000 or len(check["name"]) > 63
        ]
        if long_checks:
            blockers.append("custom_constraints:" + name)
            unsupported.extend(
                {"kind": "constraint", "object": f"public.{name}.{check['name'][:63]}"}
                for check in long_checks
            )
            table["check_constraints"] = [
                check for check in table["check_constraints"] if check not in long_checks
            ]
        if custom_defaults:
            if "custom_column_behavior:" + name not in blockers:
                blockers.append("custom_column_behavior:" + name)
            unsupported.extend(
                {"kind": "default", "object": f"public.{name}.{column}"}
                for column in custom_defaults
            )
        if table.pop("composite_foreign_keys", False):
            blockers.append("composite_foreign_keys:" + name)
            unsupported.append({"kind": "composite_foreign_key", "object": f"public.{name}"})
        for column in table["columns"]:
            column["type"] = normalize_type(column["type"])
            if column.get("default") is None:
                column.pop("default", None)
            if column.get("identity") is None:
                column.pop("identity", None)
    return DataContract(version=1, tables=infer_ownership(tables)), blockers, unsupported


def describe_live_catalog(
    backend: Any,
) -> tuple[DataContract, list[str], list[dict[str, str]]]:
    return describe_catalog(json.loads(admin_sql(backend, CATALOG_SCRIPT)))


# Runs only inside the candidate with its own data and no public egress.
# The result is validated against the real catalog and never grants ownership.
DRIZZLE_CATALOG_JS = r"""
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const root = process.env.OMNIA_WORKSPACE || '/workspace';
const ts = require(root + '/node_modules/typescript');
Module._extensions['.ts'] = (module, filename) => {
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  module._compile(source, filename);
};
const {getTableConfig, PgDialect} = require(root + '/node_modules/drizzle-orm/pg-core');
const {getTableName} = require(root + '/node_modules/drizzle-orm');
const schema = require(root + '/src/lib/db/schema.ts');
const dialect = new PgDialect();
const tables = [];
for (const item of Object.values(schema)) {
  let config; try { config = getTableConfig(item); } catch { continue; }
  if (!config?.columns?.length) continue;
  if (config.schema && config.schema !== 'public') throw Error('Unsupported schema');
  // Named CHECKs are compared with the live catalog, not refused up front.
  const checks = config.checks.map(check => {
    const rendered = dialect.sqlToQuery(check.value);
    if (rendered.params.length) throw Error('Parameterized check expression');
    return {name: check.name, definition: rendered.sql};
  });
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
  })), primary_key:primary, unique_keys:unique, check_constraints:checks,
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


# ── CHECK constraints declared in hand-written SQL migrations ─────────────────
# Generated MAX apps keep CHECKs in migrations/*.sql, not in the Drizzle schema.
# Without reading them every historical version looks like it "lost" its CHECKs.
_MIGRATION_FILE = re.compile(r"^(?:migrations|drizzle|db/migrations|src/db/migrations)/[^/]+\.sql$")
_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)'
_ADD_CHECK = re.compile(
    rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:{_IDENT}\.)?(?P<table>{_IDENT})"
    rf"\s+ADD\s+CONSTRAINT\s+(?P<name>{_IDENT})\s+CHECK\s*\(",
    re.IGNORECASE,
)
_DROP_CONSTRAINT = re.compile(
    rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:{_IDENT}\.)?(?P<table>{_IDENT})"
    rf"\s+DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(?P<name>{_IDENT})",
    re.IGNORECASE,
)
_CREATE_TABLE = re.compile(
    rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:{_IDENT}\.)?(?P<table>{_IDENT})\s*\(",
    re.IGNORECASE,
)
_INLINE_CHECK = re.compile(rf"(?:CONSTRAINT\s+(?P<name>{_IDENT})\s+)?CHECK\s*\(", re.IGNORECASE)


def _unquote(identifier: str) -> str:
    return identifier[1:-1].replace('""', '"') if identifier.startswith('"') else identifier.lower()


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    """Return the text inside the parenthesis opened just before ``start`` and the
    index after its closing parenthesis; string literals are skipped."""
    depth, index, quoted = 1, start, False
    while index < len(text):
        char = text[index]
        if quoted:
            if char == "'":
                if text[index + 1 : index + 2] == "'":
                    index += 1  # escaped quote inside the literal
                else:
                    quoted = False
        elif char == "'":
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
        index += 1
    return None


def _split_top_level(body: str) -> list[str]:
    parts, depth, quoted, current = [], 0, False, []
    for char in body:
        if quoted:
            current.append(char)
            quoted = char != "'"
            continue
        if char == "'":
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def _create_table_checks(statement: str, table: str) -> list[dict[str, str | None]]:
    """CHECKs inside CREATE TABLE, named like PostgreSQL names them."""
    opened = statement.index("(") + 1
    inside = _balanced(statement, opened)
    if inside is None:
        return []
    checks: list[dict[str, str | None]] = []
    used: set[str] = set()
    for element in _split_top_level(inside[0]):
        head = element.split(None, 1)[0].upper() if element else ""
        is_table_constraint = head in {
            "CONSTRAINT", "CHECK", "PRIMARY", "UNIQUE", "FOREIGN", "EXCLUDE",
        }
        column = None if is_table_constraint else _unquote(element.split(None, 1)[0])
        for match in _INLINE_CHECK.finditer(element):
            body = _balanced(element, match.end())
            if body is None:
                continue
            expression = " ".join(body[0].split())
            name = _unquote(match["name"]) if match["name"] else None
            if name is None:
                # column constraint: <table>_<column>_check; table-level: first
                # referenced identifier, else <table>_check (PostgreSQL's rule).
                referenced = column or next(
                    (
                        _unquote(token)
                        for token in re.findall(_IDENT, expression)
                        if token.upper() not in {"AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE",
                                                  "FALSE", "ANY", "ARRAY", "BETWEEN", "LIKE"}
                        and not token.startswith("'")
                    ),
                    None,
                )
                base = f"{table}_{referenced}_check" if referenced else f"{table}_check"
                name, suffix = base, 0
                while name in used:  # PostgreSQL appends 1, 2, ... on collision
                    suffix += 1
                    name = f"{base}{suffix}"
            used.add(name)
            checks.append({"name": name, "definition": expression})
    return checks


def migration_checks(files: dict[str, str]) -> dict[str, list[dict[str, str | None]]]:
    """table -> CHECK constraints still in force after replaying the migrations
    in file order. Only the statements we recognise are interpreted; anything
    else leaves the historical declaration as is (it then reads as a difference,
    never as a silently matching constraint)."""
    result: dict[str, dict[str, dict[str, str | None]]] = {}
    for path in sorted(p for p in files if _MIGRATION_FILE.match(p)):
        text = _strip_sql_comments(files[path])
        for statement in text.split(";"):
            compact = " ".join(statement.split())
            if not compact:
                continue
            create = _CREATE_TABLE.search(compact)
            if create:
                table = _unquote(create["table"])
                for check in _create_table_checks(compact[create.start():], table):
                    result.setdefault(table, {})[str(check["name"])] = check
                continue
            add = _ADD_CHECK.search(compact)
            if add:
                body = _balanced(compact, add.end())
                if body is not None:
                    table, name = _unquote(add["table"]), _unquote(add["name"])
                    result.setdefault(table, {})[name] = {
                        "name": name, "definition": " ".join(body[0].split()),
                    }
                continue
            drop = _DROP_CONSTRAINT.search(compact)
            if drop:
                result.get(_unquote(drop["table"]), {}).pop(_unquote(drop["name"]), None)
    return {table: list(checks.values()) for table, checks in result.items() if checks}


def _merge_migration_checks(tables: list[dict[str, Any]], files: dict[str, str]) -> None:
    declared = migration_checks(files)
    for table in tables:
        extra = declared.get(table["name"])
        if not extra:
            continue
        known = {check["name"] for check in table.get("check_constraints", [])}
        table.setdefault("check_constraints", []).extend(
            check for check in extra if check["name"] not in known
        )


def candidate_contract(backend: Any, files: dict[str, str]) -> DataContract:
    package = json.loads(files.get("package.json", "{}"))
    scripts = package.get("scripts", {})
    if any(key in scripts for key in ("prestart", "poststart", "predev", "postdev")):
        raise ValueError("historical startup hooks require adaptation")
    explicit = files.get(".omnia/data-contract.json")
    if explicit:
        # Declared CHECKs are compared with the live catalog in assess_contract.
        data = DataContract.model_validate_json(explicit).model_dump()
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
    _merge_migration_checks(tables, files)
    return DataContract(version=1, tables=infer_ownership(tables))


def normalize_type(value: str) -> str:
    # Drizzle renders "numeric(12, 2)", PostgreSQL "numeric(12,2)": same type.
    original = re.sub(r"\s*,\s*", ",", " ".join(value.strip().split()))
    original = re.sub(r"\s*\(\s*", "(", original)
    original = re.sub(r"\s*\)", ")", original)
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
