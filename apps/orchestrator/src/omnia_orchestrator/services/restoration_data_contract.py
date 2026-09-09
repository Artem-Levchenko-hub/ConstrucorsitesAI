"""A deliberately bounded, enforceable contract for historical PostgreSQL writers.

Code rollback never runs a down migration. Unknown semantics need adaptation; SQL
privileges and RLS enforce the supported column/actor contract independently of code.
"""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataColumn(StrictContract):
    name: Identifier
    type: str = Field(min_length=1, max_length=160)
    nullable: bool = True
    meaning: str | None = Field(default=None, max_length=160)
    values: list[str] | None = None
    json_keys: list[str] | None = None


class DataForeignKey(StrictContract):
    column: Identifier
    table: Identifier
    target: Identifier
    on_delete: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"] = "NO ACTION"
    on_update: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"] = "NO ACTION"


class OwnerReference(StrictContract):
    column: Identifier
    table: Identifier
    target: Identifier = "id"


class DataTable(StrictContract):
    name: Identifier
    columns: list[DataColumn] = Field(min_length=1, max_length=500)
    owner_column: Identifier | None = None
    owner_reference: OwnerReference | None = None
    read_only: bool = False
    checks: list[str] = Field(default_factory=list)
    foreign_keys: list[DataForeignKey] = Field(default_factory=list)
    primary_key: list[Identifier] = Field(default_factory=list)
    unique_keys: list[list[Identifier]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_table(self) -> Self:
        names = [column.name for column in self.columns]
        if self.name.startswith(("pg_", "sql_", "omnia_")):
            raise ValueError("reserved database name")
        if len(names) != len(set(names)):
            raise ValueError("duplicate column")
        for key in [self.primary_key, *self.unique_keys]:
            if len(key) != len(set(key)) or any(name not in names for name in key):
                raise ValueError("invalid key columns")
        if self.owner_column and self.owner_column not in names:
            raise ValueError("missing owner column")
        if self.owner_reference and self.owner_reference.column not in names:
            raise ValueError("missing owner reference")
        if bool(self.owner_column) + bool(self.owner_reference) != 1 and not self.read_only:
            raise ValueError("writable tables require exactly one actor rule")
        return self


class DataContract(StrictContract):
    version: Literal[1]
    tables: list[DataTable] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        tables = {table.name: table for table in self.tables}
        if len(tables) != len(self.tables):
            raise ValueError("duplicate table")
        for table in self.tables:
            reference = table.owner_reference
            if reference:
                parent = tables.get(reference.table)
                if (
                    parent is None
                    or parent.owner_column is None
                    or reference.target not in {column.name for column in parent.columns}
                ):
                    raise ValueError("actor references require a directly owned parent")
        return self


class ContractAssessment(BaseModel):
    blockers: list[str] = Field(default_factory=list)
    retained_columns: list[str] = Field(default_factory=list)
    blocked_deletes: list[str] = Field(default_factory=list)


def foreign_key_cycle_nodes(contract: DataContract) -> list[str]:
    """RLS follows parent SELECT policies, so even nullable FK cycles recurse."""
    graph = {
        table.name: {relation.table for relation in table.foreign_keys}
        | ({table.owner_reference.table} if table.owner_reference else set())
        for table in contract.tables
    }
    visited: set[str] = set()
    stack: list[str] = []
    active: set[str] = set()
    cycles: set[str] = set()

    def visit(name: str) -> None:
        if name in active:
            cycles.update(stack[stack.index(name) :])
            return
        if name in visited or name not in graph:
            return
        active.add(name)
        stack.append(name)
        for parent in sorted(graph[name]):
            visit(parent)
        stack.pop()
        active.remove(name)
        visited.add(name)

    for name in sorted(graph):
        visit(name)
    return sorted(cycles)


def assess_contract(old: DataContract, current: DataContract) -> ContractAssessment:
    result = ContractAssessment()
    result.blockers.extend(
        "foreign_key_cycle:" + name
        for name in sorted(
            set(foreign_key_cycle_nodes(old)) | set(foreign_key_cycle_nodes(current))
        )
    )
    current_tables = {table.name: table for table in current.tables}
    old_tables = {table.name: table for table in old.tables}
    for table in old.tables:
        live = current_tables.get(table.name)
        if live is None:
            result.blockers.append(f"missing_table:{table.name}")
            continue
        if table.owner_column != live.owner_column or table.owner_reference != live.owner_reference:
            result.blockers.append(f"actor_rule_changed:{table.name}")
        if (
            table.checks != live.checks
            or table.foreign_keys != live.foreign_keys
            or table.primary_key != live.primary_key
            or table.unique_keys != live.unique_keys
        ):
            result.blockers.append(f"constraints_changed:{table.name}")
        columns = {column.name: column for column in live.columns}
        old_names = {column.name for column in table.columns}
        for previous in table.columns:
            column = columns.get(previous.name)
            key = f"{table.name}.{previous.name}"
            if column is None:
                result.blockers.append(f"missing_column:{key}")
            elif (
                previous.type != column.type
                or previous.meaning != column.meaning
                or previous.values != column.values
                or (previous.nullable and not column.nullable)
            ):
                result.blockers.append(f"column_contract_changed:{key}")
            elif column.type in {"json", "jsonb"} and (
                previous.json_keys is None
                or column.json_keys is None
                or not set(previous.json_keys).issubset(column.json_keys)
            ):
                result.blockers.append(f"json_write_contract_missing:{key}")
            if (
                column is not None
                and column.json_keys is not None
                and previous.json_keys is not None
                and set(column.json_keys) - set(previous.json_keys)
            ):
                result.blocked_deletes.append(table.name)
        for column in live.columns:
            if column.name not in old_names:
                key = f"{table.name}.{column.name}"
                result.retained_columns.append(key)
                result.blocked_deletes.append(table.name)
                # A default can fabricate a surname/status, so it is not evidence
                # that an old insert has preserved the meaning of a required field.
                if not column.nullable:
                    result.blockers.append(f"new_required_column:{key}")
    for table in current.tables:
        previous_table = old_tables.get(table.name)
        unknown = previous_table is None or {column.name for column in table.columns} - {
            column.name for column in previous_table.columns
        }
        for relation in table.foreign_keys:
            if (
                unknown
                and relation.table in old_tables
                and relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}
            ):
                result.blocked_deletes.append(relation.table)
    protected = set(result.blocked_deletes) | (set(current_tables) - set(old_tables))
    protected.update(
        table.name
        for table in current.tables
        if any(column.type in {"json", "jsonb"} for column in table.columns)
    )
    # Existing rows may predate actor checks. Never let deleting one actor's
    # parent cascade into independently-owned rows of another actor.
    for table in current.tables:
        for relation in table.foreign_keys:
            parent = current_tables.get(relation.table)
            if (
                table.owner_column
                and parent
                and parent.owner_column
                and relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}
            ):
                protected.add(parent.name)
    changed = True
    while changed:
        changed = False
        for table in current.tables:
            if table.name not in protected:
                continue
            for relation in table.foreign_keys:
                if relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}:
                    if relation.table not in protected:
                        protected.add(relation.table)
                        changed = True
    result.blocked_deletes = sorted(protected & set(old_tables))
    return result


def qi(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", value):
        raise ValueError("unsafe SQL identifier")
    return '"' + value + '"'


def ql(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL in SQL literal")
    return "'" + value.replace("'", "''") + "'"


def database_policy_sql(
    contract: DataContract,
    *,
    epoch: int,
    project_id: str,
    token_secret: str,
    password: str,
    blocked_deletes: list[str] | None = None,
) -> str:
    """Execute only from the controller's Unix socket, as the dedicated PG owner.

    The caller installs controller-owned pg_hba.conf, stops former writers, and
    terminates old sessions before switching credentials. Never log this SQL.
    """
    if type(epoch) is not int or epoch < 1:
        raise ValueError("invalid epoch")
    if foreign_key_cycle_nodes(contract):
        raise ValueError("foreign key actor cycle requires adaptation")
    blocked_deletes = sorted(
        set(blocked_deletes or []) | set(assess_contract(contract, contract).blocked_deletes)
    )
    lines = [
        "BEGIN; SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '30s';",
        "CREATE SCHEMA IF NOT EXISTS omnia_guard AUTHORIZATION postgres;",
        "ALTER SCHEMA omnia_guard OWNER TO postgres;",
        "REVOKE ALL ON SCHEMA omnia_guard FROM PUBLIC;",
        "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA omnia_guard;",
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='omnia_runtime') "
        "THEN CREATE ROLE omnia_runtime; END IF; END $$;",
        "ALTER ROLE omnia_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD " + ql(password) + ";",
        "ALTER ROLE omnia_runtime SET statement_timeout = '15s';",
        "ALTER ROLE omnia_runtime SET idle_in_transaction_session_timeout = '15s';",
        _runtime_ownership_preflight(contract),
        "DO $$ DECLARE n text; BEGIN "
        "EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC, omnia_runtime', "
        "current_database()); "
        "EXECUTE format('GRANT CONNECT ON DATABASE %I TO omnia_runtime', current_database()); "
        "FOR n IN SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' "
        "AND nspname <> 'information_schema' LOOP "
        "EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC, omnia_runtime', n); "
        "EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM PUBLIC, omnia_runtime', n); "
        "EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM PUBLIC, omnia_runtime', n); "
        "EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC, omnia_runtime', n); "
        "EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I "
        "REVOKE ALL ON TABLES FROM PUBLIC', n); "
        "EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', n); "
        "END LOOP; "
        "FOR n IN SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
        "JOIN pg_roles u ON u.oid=m.member WHERE u.rolname='omnia_runtime' LOOP "
        "EXECUTE format('REVOKE %I FROM omnia_runtime', n); END LOOP; END $$;",
        _hmac_wrapper_sql(),
        "GRANT USAGE ON SCHEMA public, omnia_guard TO omnia_runtime;",
        "CREATE TABLE IF NOT EXISTS omnia_guard.identity "
        "(singleton boolean PRIMARY KEY CHECK(singleton), project_id text NOT NULL, "
        "epoch bigint NOT NULL, token_secret text NOT NULL);",
        "ALTER TABLE omnia_guard.identity OWNER TO postgres;",
        "REVOKE ALL ON omnia_guard.identity FROM PUBLIC, omnia_runtime;",
        """DO $identity$ BEGIN
IF EXISTS (SELECT FROM pg_trigger WHERE tgrelid='omnia_guard.identity'::regclass
           AND NOT tgisinternal)
  OR EXISTS (SELECT FROM pg_rewrite WHERE ev_class='omnia_guard.identity'::regclass)
  OR EXISTS (SELECT FROM pg_class WHERE oid='omnia_guard.identity'::regclass
             AND (relkind <> 'r' OR relispartition))
  OR EXISTS (SELECT FROM pg_attrdef WHERE adrelid='omnia_guard.identity'::regclass)
  OR (SELECT count(*) FROM pg_attribute WHERE attrelid='omnia_guard.identity'::regclass
      AND attnum>0 AND NOT attisdropped) <> 4
  OR (SELECT count(*) FROM pg_attribute WHERE attrelid='omnia_guard.identity'::regclass
      AND attnum>0 AND NOT attisdropped AND attnotnull
      AND ((attname='singleton' AND atttypid='bool'::regtype)
        OR (attname='epoch' AND atttypid='int8'::regtype)
        OR (attname IN ('project_id','token_secret') AND atttypid='text'::regtype))) <> 4
  OR (SELECT count(*) FROM pg_constraint WHERE conrelid='omnia_guard.identity'::regclass) <> 2
  OR EXISTS (SELECT FROM pg_constraint WHERE conrelid='omnia_guard.identity'::regclass
      AND (NOT convalidated OR condeferrable OR pg_get_constraintdef(oid,true)
           NOT IN ('PRIMARY KEY (singleton)', 'CHECK (singleton)')))
  OR EXISTS (SELECT FROM pg_index WHERE indrelid='omnia_guard.identity'::regclass
      AND (NOT indisprimary OR indexprs IS NOT NULL OR indpred IS NOT NULL))
THEN RAISE EXCEPTION 'Untrusted controller identity relation'; END IF;
END $identity$;""",
        "INSERT INTO omnia_guard.identity VALUES (true, "
        + ql(project_id)
        + ", "
        + str(epoch)
        + ", "
        + ql(token_secret)
        + ") ON CONFLICT(singleton) DO UPDATE SET "
        "project_id=excluded.project_id, epoch=excluded.epoch, token_secret=excluded.token_secret;",
        """CREATE OR REPLACE FUNCTION omnia_guard.actor() RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,omnia_guard AS $guard$
DECLARE token text; parts text[]; decoded jsonb; expected text; identity omnia_guard.identity;
BEGIN
  token := current_setting('omnia.actor_token', true);
  IF token IS NULL OR length(token)>4096 THEN RETURN NULL; END IF;
  parts := string_to_array(token, '.');
  IF array_length(parts, 1) <> 2 THEN RETURN NULL; END IF;
  SELECT * INTO STRICT identity FROM omnia_guard.identity WHERE singleton;
  expected := omnia_guard.token_mac(parts[1], identity.token_secret);
  IF expected <> parts[2] THEN RETURN NULL; END IF;
  decoded := convert_from(decode(parts[1], 'hex'), 'UTF8')::jsonb;
  IF decoded->>'purpose' <> 'omnia-data' OR decoded->>'project_id' <> identity.project_id
    OR (decoded->>'epoch')::bigint <> identity.epoch
    OR (decoded->>'expires_at')::bigint <= extract(epoch FROM clock_timestamp())
    OR (decoded->>'expires_at')::bigint > extract(epoch FROM clock_timestamp()) + 120
    OR decoded->>'user_id' !~ '^[A-Za-z0-9_-]{1,128}$'
  THEN RETURN NULL; END IF;
  RETURN decoded->>'user_id';
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $guard$;
ALTER FUNCTION omnia_guard.actor() OWNER TO postgres;
REVOKE ALL ON FUNCTION omnia_guard.actor() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION omnia_guard.actor() TO omnia_runtime;""",
    ]
    tables = {table.name: table for table in contract.tables}
    for table in contract.tables:
        name = "public." + qi(table.name)
        columns = ", ".join(qi(column.name) for column in table.columns)
        lines += [
            f"ALTER TABLE {name} OWNER TO postgres;",
            f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY;",
            f"DROP POLICY IF EXISTS omnia_restore_actor ON {name};",
            f"DROP POLICY IF EXISTS omnia_restore_limit ON {name};",
        ]
        if table.owner_column:
            actor = qi(table.owner_column) + "::text = omnia_guard.actor()"
        elif table.owner_reference:
            ref = table.owner_reference
            parent = tables[ref.table]
            owner = qi(str(parent.owner_column))
            # A correlated EXISTS through the parent's RLS remains actor-bound.
            actor = (
                f"EXISTS (SELECT 1 FROM public.{qi(ref.table)} AS parent WHERE "
                f"parent.{qi(ref.target)} = {qi(table.name)}.{qi(ref.column)} "
                f"AND parent.{owner}::text = omnia_guard.actor())"
            )
        else:
            actor = "omnia_guard.actor() IS NOT NULL"
        for index, relation in enumerate(table.foreign_keys):
            if relation.table not in tables or relation.table == table.name:
                raise ValueError("foreign actor relationship requires adaptation")
            alias = "foreign_parent_" + str(index)
            local = qi(table.name) + "." + qi(relation.column)
            actor += (
                f" AND ({local} IS NULL OR EXISTS (SELECT 1 FROM public.{qi(relation.table)} "
                f"AS {alias} WHERE {alias}.{qi(relation.target)} = {local}))"
            )
        # Restrictive AND policy prevents a preexisting permissive policy from
        # broadening access. A permissive policy is also required by PostgreSQL.
        lines += [
            f"CREATE POLICY omnia_restore_actor ON {name} TO omnia_runtime "
            f"USING ({actor}) WITH CHECK ({actor});",
            f"CREATE POLICY omnia_restore_limit ON {name} AS RESTRICTIVE TO omnia_runtime "
            f"USING ({actor}) WITH CHECK ({actor});",
            f"GRANT SELECT ({columns}) ON {name} TO omnia_runtime;",
        ]
        if not table.read_only:
            lines += [
                f"GRANT INSERT ({columns}) ON {name} TO omnia_runtime;",
                _update_grants(table),
            ]
            independent_parent = any(
                child.owner_column
                and table.owner_column
                and relation.table == table.name
                and relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}
                for child in contract.tables
                for relation in child.foreign_keys
            )
            if (
                table.name not in (blocked_deletes or [])
                and not independent_parent
                and not any(column.type in {"json", "jsonb"} for column in table.columns)
            ):
                lines.append(f"GRANT DELETE ON {name} TO omnia_runtime;")
            for column in table.columns:
                if column.type in {"json", "jsonb"} and column.json_keys is not None:
                    lines.extend(_json_guard(table.name, column))
    lines.append("COMMIT;")
    return "\n".join(lines)


def _update_grants(table: DataTable) -> str:
    allowed = [
        column.name
        for column in table.columns
        if column.name != table.owner_column
        and (not table.owner_reference or column.name != table.owner_reference.column)
    ]
    names = "ARRAY[" + ",".join(ql(name) for name in allowed) + "]::text[]"
    target = "public." + qi(table.name)
    # Consult live constraints too: historical contracts cannot enumerate new
    # foreign keys whose ON UPDATE action would mutate hidden dependent rows.
    return f"""DO $updates$ DECLARE permitted text; BEGIN
SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO permitted
FROM pg_attribute a WHERE a.attrelid={ql(target)}::regclass AND a.attnum>0
 AND NOT a.attisdropped AND a.attname=ANY({names})
 AND NOT EXISTS (SELECT FROM pg_constraint k WHERE
   (k.conrelid=a.attrelid AND k.contype IN ('p','f') AND a.attnum=ANY(k.conkey))
   OR (k.confrelid=a.attrelid AND k.contype='f' AND a.attnum=ANY(k.confkey)));
IF permitted IS NOT NULL THEN
 EXECUTE 'GRANT UPDATE (' || permitted || ') ON {target} TO omnia_runtime';
END IF; END $updates$;"""


def json_guard_function_name(table: str, column: str) -> str:
    # Long identifiers must not alias two different table/column guards.
    digest = hashlib.sha256(f"{table}\0{column}".encode()).hexdigest()[:32]
    return "json_" + digest


def _runtime_ownership_preflight(contract: DataContract) -> str:
    tables = "ARRAY[" + ",".join(ql(table.name) for table in contract.tables) + "]::text[]"
    guards = ["actor"] + [
        json_guard_function_name(table.name, column.name)
        for table in contract.tables
        for column in table.columns
        if column.type in {"json", "jsonb"} and column.json_keys is not None
    ]
    functions = "ARRAY[" + ",".join(ql(name) for name in guards) + "]::text[]"
    # Revoking ACLs cannot remove an owner's implicit grant option. Never promote
    # unknown SECURITY DEFINER code to postgres just to take away its ownership.
    return f"""DO $ownership$ BEGIN
IF EXISTS (SELECT FROM pg_namespace WHERE nspowner='omnia_runtime'::regrole
           AND nspname <> 'omnia_guard')
 OR EXISTS (
   SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE c.relowner='omnia_runtime'::regrole AND c.relkind NOT IN ('i','I','t')
     AND NOT (c.relkind='r' AND
       ((n.nspname='public' AND c.relname=ANY({tables}))
        OR (n.nspname='omnia_guard' AND c.relname='identity'))))
 OR EXISTS (
   SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE p.proowner='omnia_runtime'::regrole AND NOT
     (n.nspname='omnia_guard' AND p.prokind='f' AND
       ((p.proname=ANY({functions}) AND p.pronargs=0)
         OR (p.proname='token_mac' AND p.proargtypes='25 25'::oidvector))))
 OR EXISTS (SELECT FROM pg_type WHERE typowner='omnia_runtime'::regrole
            AND (typtype IN ('d','e','r','m') OR (typtype='b' AND typelem=0)))
THEN RAISE EXCEPTION 'Runtime role owns unmanaged database objects'; END IF;
END $ownership$;"""


def _hmac_wrapper_sql() -> str:
    """Bind the extension's real function without moving an existing extension."""
    return """DO $hmac$ DECLARE extension_schema text; BEGIN
SELECT n.nspname INTO STRICT extension_schema
FROM pg_extension e
JOIN pg_depend d ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid
  AND d.classid='pg_proc'::regclass AND d.deptype='e'
JOIN pg_proc p ON p.oid=d.objid
JOIN pg_namespace n ON n.oid=p.pronamespace
JOIN pg_language l ON l.oid=p.prolang
WHERE e.extname='pgcrypto' AND p.proname='hmac' AND p.proargtypes='25 25 25'::oidvector
  AND p.prorettype='bytea'::regtype AND l.lanname='c'
  AND p.prosrc='pg_hmac' AND p.probin='$libdir/pgcrypto' AND NOT p.prosecdef;
EXECUTE format('ALTER FUNCTION %I.hmac(text,text,text) OWNER TO postgres', extension_schema);
EXECUTE format(
  'CREATE OR REPLACE FUNCTION omnia_guard.token_mac(value text, key text) RETURNS text '
  'LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS %L',
  format('SELECT pg_catalog.encode(%I.hmac($1::text,$2::text,''sha256''::text),''hex'')',
         extension_schema));
ALTER FUNCTION omnia_guard.token_mac(text,text) OWNER TO postgres;
REVOKE ALL ON FUNCTION omnia_guard.token_mac(text,text) FROM PUBLIC, omnia_runtime;
END $hmac$;"""


def _json_guard(table: str, column: DataColumn) -> list[str]:
    name = qi(column.name)
    keys = "ARRAY[" + ",".join(ql(key) for key in column.json_keys or []) + "]::text[]"
    function = qi(json_guard_function_name(table, column.name))
    return [
        f"""CREATE OR REPLACE FUNCTION omnia_guard.{function}() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,omnia_guard AS $json$
DECLARE retained jsonb; key text;
BEGIN
  IF NEW.{name} IS NOT NULL AND jsonb_typeof(NEW.{name}::jsonb) <> 'object' THEN
    RAISE EXCEPTION 'JSON object required'; END IF;
  FOREACH key IN ARRAY {keys} LOOP
    IF TG_OP='INSERT' THEN
      IF jsonb_typeof(NEW.{name}::jsonb -> key) IN ('object','array') THEN
        RAISE EXCEPTION 'Nested JSON writes require adaptation'; END IF;
    ELSIF (jsonb_typeof(NEW.{name}::jsonb -> key) IN ('object','array')
      OR jsonb_typeof(OLD.{name}::jsonb -> key) IN ('object','array'))
      AND (NEW.{name}::jsonb -> key) IS DISTINCT FROM (OLD.{name}::jsonb -> key) THEN
      RAISE EXCEPTION 'Nested JSON changes require adaptation';
    END IF;
  END LOOP;
  retained := CASE WHEN TG_OP='UPDATE' THEN coalesce(OLD.{name}::jsonb, '{{}}') - {keys}
    ELSE '{{}}'::jsonb END;
  IF (coalesce(NEW.{name}::jsonb, '{{}}') - {keys}) <> '{{}}'::jsonb
    AND (coalesce(NEW.{name}::jsonb, '{{}}') - {keys}) <> retained THEN
    RAISE EXCEPTION 'Unknown JSON fields cannot be changed'; END IF;
  NEW.{name} := retained || (coalesce(NEW.{name}::jsonb, '{{}}') - ARRAY(
    SELECT jsonb_object_keys(retained)));
  RETURN NEW;
END $json$;""",
        f"ALTER FUNCTION omnia_guard.{function}() OWNER TO postgres;",
        f"REVOKE ALL ON FUNCTION omnia_guard.{function}() FROM PUBLIC;",
        f"DROP TRIGGER IF EXISTS {function} ON public.{qi(table)};",
        f"CREATE TRIGGER {function} BEFORE INSERT OR UPDATE ON public.{qi(table)} "
        f"FOR EACH ROW EXECUTE FUNCTION omnia_guard.{function}();",
    ]
