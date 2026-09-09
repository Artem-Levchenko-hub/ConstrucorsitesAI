"""Real PostgreSQL policy proof, only in an explicitly disposable database.

No API conftest, no fallback DSN, no generation. Never point this fixture at a
shared/production cluster: the controller policy manages a cluster login role.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

from omnia_orchestrator.services.restoration_data_contract import (
    DataContract,
    assess_contract,
    database_policy_sql,
)

PROJECT = "restoration-policy-fixture"
TOKEN_SECRET = "isolated-fixture-controller-signing-key"
PASSWORD = "isolated-fixture-runtime-password"
A = UUID("00000000-0000-0000-0000-000000000001")
NEW = UUID("00000000-0000-0000-0000-000000000002")
B = UUID("00000000-0000-0000-0000-000000000003")


def contracts():
    contact = {
        "name": "contacts",
        "owner_column": "owner_id",
        "columns": [
            {"name": "id", "type": "uuid", "nullable": False},
            {"name": "owner_id", "type": "text", "nullable": False},
            {"name": "name", "type": "text"},
            {"name": "metadata", "type": "jsonb", "json_keys": ["note"]},
        ],
    }
    tasks = {
        "name": "tasks",
        "owner_reference": {"column": "contact_id", "table": "contacts"},
        "columns": [
            {"name": "id", "type": "uuid", "nullable": False},
            {"name": "contact_id", "type": "uuid", "nullable": False},
            {"name": "title", "type": "text"},
        ],
        "foreign_keys": [
            {"column": "contact_id", "table": "contacts", "target": "id", "on_delete": "CASCADE"}
        ],
    }
    old = DataContract.model_validate({"version": 1, "tables": [contact, tasks]})
    current_contact = json.loads(json.dumps(contact))
    current_contact["columns"].append({"name": "surname", "type": "text"})
    current_contact["columns"][3]["json_keys"].append("new_field")
    invoices = {
        "name": "invoices",
        "owner_reference": {"column": "contact_id", "table": "contacts"},
        "columns": [
            {"name": "id", "type": "uuid", "nullable": False},
            {"name": "contact_id", "type": "uuid", "nullable": False},
        ],
        "foreign_keys": [
            {"column": "contact_id", "table": "contacts", "target": "id", "on_delete": "CASCADE"}
        ],
    }
    current = DataContract.model_validate(
        {"version": 1, "tables": [current_contact, tasks, invoices]}
    )
    return old, current


def actor_token(user="user_a", **changes):
    payload = {
        "purpose": "omnia-data",
        "project_id": PROJECT,
        "epoch": 7,
        "expires_at": int(time.time()) + 60,
        "user_id": user,
    }
    payload.update(changes)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode().hex()
    signature = hmac.new(TOKEN_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


@dataclass
class Database:
    admin: asyncpg.Connection
    runtime: asyncpg.Connection

    async def actor(self, token):
        await self.runtime.execute("SELECT set_config('omnia.actor_token', $1, false)", token)


@pytest_asyncio.fixture
async def database(request):
    dsn = os.environ.get("RESTORATION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires explicit disposable RESTORATION_TEST_DATABASE_URL")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgresql", "postgres"} or parsed.path != "/restoration_policy_test":
        pytest.fail("RESTORATION_TEST_DATABASE_URL must name restoration_policy_test")
    admin = await asyncpg.connect(dsn, timeout=10, command_timeout=30)
    runtime = None
    try:
        assert await admin.fetchval("SELECT current_database()") == "restoration_policy_test"
        assert await admin.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        await admin.execute(
            "DROP SCHEMA IF EXISTS omnia_guard CASCADE; "
            "DROP SCHEMA IF EXISTS unmanaged CASCADE; "
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
        )
        await admin.execute("""
            CREATE TABLE public.contacts (
                id uuid PRIMARY KEY, owner_id text NOT NULL, name text,
                surname text, metadata jsonb
            );
            CREATE TABLE public.tasks (
                id uuid PRIMARY KEY,
                contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                title text
            );
            CREATE TABLE public.invoices (
                id uuid PRIMARY KEY,
                contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE
            );
            ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
            CREATE POLICY old_permissive_policy ON contacts USING (true) WITH CHECK (true);
            CREATE FUNCTION public.old_owner_bypass() RETURNS bigint
              LANGUAGE sql SECURITY DEFINER AS 'SELECT count(*) FROM public.contacts';
        """)
        await admin.executemany(
            "INSERT INTO contacts VALUES($1,$2,$3,$4,$5::jsonb)",
            [
                (A, "user_a", "Before", "Preserved surname", '{"note":"old","new_field":"keep"}'),
                (NEW, "user_a", "Newer record", "New surname", '{"note":"new","new_field":42}'),
                (B, "user_b", "Foreign", "Foreign surname", '{"note":"other"}'),
            ],
        )
        await admin.executemany(
            "INSERT INTO tasks VALUES($1,$2,$3)",
            [
                (A, A, "Owned task"),
                (B, B, "Foreign task"),
            ],
        )
        await admin.execute("INSERT INTO invoices VALUES($1,$2)", A, A)
        if getattr(request, "param", None) == "preexisting_pgcrypto_public":
            await admin.execute("CREATE EXTENSION pgcrypto WITH SCHEMA public")
        old, current = contracts()
        assessment = assess_contract(old, current)
        assert not assessment.blockers and assessment.blocked_deletes == ["contacts"]
        # Never print policy SQL: even fixture credentials should stay out of command output.
        await admin.execute(
            database_policy_sql(
                old,
                epoch=7,
                project_id=PROJECT,
                token_secret=TOKEN_SECRET,
                password=PASSWORD,
                blocked_deletes=assessment.blocked_deletes,
            )
        )
        if getattr(request, "param", None) == "preexisting_runtime_owner":
            # An older privileged application may have pre-created reserved objects.
            await admin.execute(
                "ALTER SCHEMA omnia_guard OWNER TO omnia_runtime; "
                "ALTER TABLE omnia_guard.identity OWNER TO omnia_runtime; "
                "ALTER FUNCTION omnia_guard.actor() OWNER TO omnia_runtime;"
            )
            await admin.execute(
                database_policy_sql(
                    old,
                    epoch=7,
                    project_id=PROJECT,
                    token_secret=TOKEN_SECRET,
                    password=PASSWORD,
                    blocked_deletes=assessment.blocked_deletes,
                )
            )
        runtime = await asyncpg.connect(
            dsn, user="omnia_runtime", password=PASSWORD, timeout=10, command_timeout=10
        )
        yield Database(admin, runtime)
    finally:
        if runtime is not None:
            await runtime.close()
        await admin.close()


@pytest.mark.parametrize("database", [None, "preexisting_pgcrypto_public"], indirect=True)
async def test_old_writer_preserves_new_column_rows_and_json(database):
    await database.actor(actor_token())
    assert (
        await database.runtime.execute("UPDATE contacts SET name=$1 WHERE id=$2", "After", A)
        == "UPDATE 1"
    )
    await database.runtime.execute(
        "UPDATE contacts SET metadata=$1::jsonb WHERE id=$2", '{"note":"updated"}', A
    )
    result = await database.admin.fetchrow(
        "SELECT name,surname,metadata FROM contacts WHERE id=$1", A
    )
    assert (result["name"], result["surname"]) == ("After", "Preserved surname")
    assert json.loads(result["metadata"]) == {"note": "updated", "new_field": "keep"}
    assert await database.admin.fetchval("SELECT count(*) FROM contacts") == 3
    assert (
        await database.admin.fetchval("SELECT surname FROM contacts WHERE id=$1", NEW)
        == "New surname"
    )
    # The historical writer can still create an actor-owned record without inventing new fields.
    extra = UUID("00000000-0000-0000-0000-000000000004")
    await database.runtime.execute(
        "INSERT INTO contacts(id,owner_id,name,metadata) VALUES($1,$2,$3,$4::jsonb)",
        extra,
        "user_a",
        "Inserted",
        '{"note":"insert"}',
    )
    assert await database.admin.fetchval("SELECT surname FROM contacts WHERE id=$1", extra) is None


@pytest.mark.parametrize("database", ["preexisting_pgcrypto_public"], indirect=True)
async def test_existing_extension_namespace_is_preserved(database):
    namespace = await database.admin.fetchval("""
        SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
        WHERE e.extname='pgcrypto'
    """)
    assert namespace == "public"
    assert (
        await database.admin.fetchval("SELECT public.hmac('x','y','sha256')")
        == hmac.new(
            b"y",
            b"x",
            hashlib.sha256,
        ).digest()
    )


async def test_actor_rls_blocks_cross_user_read_update_and_owner_rebinding(database):
    await database.actor(actor_token())
    assert set(await database.runtime.fetchval("SELECT array_agg(id) FROM contacts")) == {A, NEW}
    assert await database.runtime.fetchval("SELECT array_agg(id) FROM tasks") == [A]
    assert (
        await database.runtime.execute("UPDATE contacts SET name=$1 WHERE id=$2", "Wrong", B)
        == "UPDATE 0"
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("UPDATE contacts SET owner_id=$1 WHERE id=$2", "user_b", A)
    await database.actor(actor_token("user_b"))
    assert await database.runtime.fetchval("SELECT array_agg(id) FROM contacts") == [B]
    assert await database.runtime.fetchval("SELECT array_agg(id) FROM tasks") == [B]


@pytest.mark.parametrize(
    "case", ["missing", "forged", "expired", "epoch", "project", "purpose", "future"]
)
async def test_invalid_actor_cannot_read_or_write(database, case):
    token = {
        "missing": "",
        "forged": actor_token()[:-1] + ("0" if actor_token()[-1] != "0" else "1"),
        "expired": actor_token(expires_at=int(time.time()) - 1),
        "epoch": actor_token(epoch=6),
        "project": actor_token(project_id="another-project"),
        "purpose": actor_token(purpose="other-purpose"),
        "future": actor_token(expires_at=int(time.time()) + 300),
    }[case]
    await database.actor(token)
    assert await database.runtime.fetchval("SELECT count(id) FROM contacts") == 0
    assert (
        await database.runtime.execute("UPDATE contacts SET name=$1 WHERE id=$2", "Wrong", A)
        == "UPDATE 0"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.bad(id int)",
        "CREATE TEMP TABLE bad(id int)",
        "ALTER TABLE contacts ADD COLUMN bad text",
        "TRUNCATE contacts CASCADE",
        "SET ROLE postgres",
        "CREATE ROLE restoration_escape",
        "COPY contacts TO PROGRAM 'true'",
        "SELECT token_secret FROM omnia_guard.identity",
        "SELECT public.old_owner_bypass()",
        "SELECT surname FROM contacts",
        "SET session_replication_role = 'replica'",
    ],
)
async def test_runtime_privileges_cannot_bypass_controller(database, statement):
    await database.actor(actor_token())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute(statement)
    assert await database.admin.fetchval("SELECT count(*) FROM contacts") == 3


async def test_old_delete_cannot_cascade_new_invoices(database):
    await database.actor(actor_token())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("DELETE FROM contacts WHERE id=$1", A)
    assert await database.admin.fetchval("SELECT count(*) FROM invoices") == 1
    assert await database.admin.fetchval("SELECT count(*) FROM contacts") == 3


@pytest.mark.parametrize(
    "metadata",
    ['{"note":"safe","new_field":"overwrite"}', '{"note":"safe","unknown":"insert"}', "[]"],
)
async def test_old_json_writer_cannot_change_unknown_fields(database, metadata):
    await database.actor(actor_token())
    with pytest.raises(asyncpg.RaiseError):
        await database.runtime.execute(
            "UPDATE contacts SET metadata=$1::jsonb WHERE id=$2", metadata, A
        )
    assert json.loads(
        await database.admin.fetchval("SELECT metadata FROM contacts WHERE id=$1", A)
    ) == {
        "note": "old",
        "new_field": "keep",
    }


def test_changed_semantic_contract_requires_adaptation():
    old, current = contracts()
    changed = current.model_dump()
    changed["tables"][0]["columns"][2]["meaning"] = "different business interpretation"
    assessment = assess_contract(old, DataContract.model_validate(changed))
    assert "column_contract_changed:contacts.name" in assessment.blockers


@pytest.mark.parametrize("database", ["preexisting_runtime_owner"], indirect=True)
async def test_preexisting_runtime_ownership_cannot_regrant_secret_access(database):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("GRANT SELECT ON omnia_guard.identity TO omnia_runtime")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.fetchval("SELECT token_secret FROM omnia_guard.identity")


@pytest.mark.parametrize("database", ["preexisting_runtime_owner"], indirect=True)
async def test_preexisting_runtime_ownership_cannot_replace_actor(database):
    await database.runtime.execute("GRANT CREATE ON SCHEMA omnia_guard TO omnia_runtime")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("""
            CREATE OR REPLACE FUNCTION omnia_guard.actor() RETURNS text
            LANGUAGE sql SECURITY DEFINER AS 'SELECT ''user_b''::text'
        """)


def test_long_column_and_table_names_do_not_share_guard_function():
    from omnia_orchestrator.services.restoration_data_contract import json_guard_function_name

    prefix = "same_" + "x" * 24
    assert json_guard_function_name(prefix + "a", prefix + "a") != json_guard_function_name(
        prefix + "b",
        prefix + "a",
    )
    assert json_guard_function_name(prefix + "a", prefix + "a") != json_guard_function_name(
        prefix + "a",
        prefix + "b",
    )


@pytest.mark.parametrize("object_kind", ["schema", "table", "function", "domain"])
async def test_unmanaged_runtime_owned_objects_are_rejected_without_promotion(
    database, object_kind
):
    statements = {
        "schema": "CREATE SCHEMA unmanaged AUTHORIZATION omnia_runtime",
        "table": (
            "CREATE TABLE public.unmanaged(id int); "
            "ALTER TABLE public.unmanaged OWNER TO omnia_runtime"
        ),
        "function": (
            "CREATE FUNCTION public.unmanaged() RETURNS int LANGUAGE sql "
            "SECURITY DEFINER AS 'SELECT 1'; "
            "ALTER FUNCTION public.unmanaged() OWNER TO omnia_runtime"
        ),
        "domain": (
            "CREATE DOMAIN public.unmanaged AS int; "
            "ALTER DOMAIN public.unmanaged OWNER TO omnia_runtime"
        ),
    }
    await database.admin.execute(statements[object_kind])
    old, current = contracts()
    sql = database_policy_sql(
        old,
        epoch=8,
        project_id=PROJECT,
        token_secret="replacement-fixture",
        password=PASSWORD,
        blocked_deletes=assess_contract(old, current).blocked_deletes,
    )
    try:
        with pytest.raises(asyncpg.RaiseError, match="unmanaged database objects"):
            await database.admin.execute(sql)
    finally:
        await database.admin.execute("ROLLBACK")
    assert await database.admin.fetchval("SELECT epoch FROM omnia_guard.identity") == 7
    if object_kind == "function":
        assert (
            await database.admin.fetchval(
                "SELECT proowner::regrole::text FROM pg_proc "
                "WHERE oid='public.unmanaged()'::regprocedure"
            )
            == "omnia_runtime"
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE contacts SET id='00000000-0000-0000-0000-000000000001' WHERE id=$1",
        "UPDATE contacts SET owner_id='user_a' WHERE id=$1",
        "UPDATE tasks SET contact_id=$1 WHERE id=$1",
        "DELETE FROM contacts WHERE id=$1",
    ],
)
async def test_identity_and_delete_reinsert_cannot_erase_hidden_data(database, statement):
    await database.actor(actor_token())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute(statement, NEW if statement.startswith("DELETE") else A)
    assert (
        await database.admin.fetchval("SELECT surname FROM contacts WHERE id=$1", NEW)
        == "New surname"
    )


async def test_actual_catalog_recognizes_only_unchanged_controller_json_trigger(database):
    from omnia_orchestrator.services.restoration_catalog import CATALOG_SQL, contract_from_catalog
    from omnia_orchestrator.services.restoration_data_contract import json_guard_function_name

    old, _ = contracts()
    payload = json.loads(await database.admin.fetchval(CATALOG_SQL))
    actual, blockers = contract_from_catalog(payload, old)
    assert blockers == []
    contact = next(table for table in actual.tables if table.name == "contacts")
    assert contact.primary_key == ["id"]
    assert next(c for c in contact.columns if c.name == "metadata").json_keys == ["note"]
    name = json_guard_function_name("contacts", "metadata")
    await database.admin.execute(
        f'CREATE OR REPLACE FUNCTION omnia_guard."{name}"() RETURNS trigger '
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,omnia_guard "
        "AS $$BEGIN RETURN NEW; END$$"
    )
    _, blockers = contract_from_catalog(json.loads(await database.admin.fetchval(CATALOG_SQL)), old)
    assert "custom_triggers:contacts" in blockers


@pytest.mark.parametrize("json_growth", [False, True])
async def test_transitive_delete_cascades_cannot_erase_hidden_fields(database, json_growth):
    await database.admin.execute("""
      CREATE TABLE a(id uuid PRIMARY KEY, owner_id text NOT NULL);
      CREATE TABLE b(id uuid PRIMARY KEY, owner_id text NOT NULL,
                     a_id uuid REFERENCES a(id) ON DELETE CASCADE, profile jsonb);
      CREATE TABLE c(id uuid PRIMARY KEY, owner_id text NOT NULL,
                     b_id uuid REFERENCES b(id) ON DELETE CASCADE, hidden text);
      INSERT INTO a VALUES('00000000-0000-0000-0000-000000000001','user_a');
      INSERT INTO b VALUES('00000000-0000-0000-0000-000000000002','user_a',
        '00000000-0000-0000-0000-000000000001','{"old":1,"new":2}');
      INSERT INTO c VALUES('00000000-0000-0000-0000-000000000003','user_a',
        '00000000-0000-0000-0000-000000000002','retained');
    """)
    tables = []
    for name, parent in (("a", None), ("b", "a"), ("c", "b")):
        columns = [{"name": "id", "type": "uuid"}, {"name": "owner_id", "type": "text"}]
        refs = []
        if parent:
            columns.append({"name": parent + "_id", "type": "uuid"})
            refs = [
                {"column": parent + "_id", "table": parent, "target": "id", "on_delete": "CASCADE"}
            ]
        if name == "b":
            columns.append({"name": "profile", "type": "jsonb", "json_keys": ["old"]})
        if name == "c" and json_growth:
            columns.append({"name": "hidden", "type": "text"})
        tables.append(
            {"name": name, "columns": columns, "owner_column": "owner_id", "foreign_keys": refs}
        )
    old = DataContract(version=1, tables=tables)
    live = old.model_dump()
    if json_growth:
        live["tables"][1]["columns"][-1]["json_keys"].append("new")
    else:
        live["tables"][2]["columns"].append({"name": "hidden", "type": "text"})
    assessment = assess_contract(old, DataContract.model_validate(live))
    assert "a" in assessment.blocked_deletes and "b" in assessment.blocked_deletes
    await database.admin.execute(
        database_policy_sql(
            old,
            epoch=7,
            project_id=PROJECT,
            token_secret=TOKEN_SECRET,
            password=PASSWORD,
            blocked_deletes=assessment.blocked_deletes,
        )
    )
    await database.actor(actor_token())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("DELETE FROM a")
    assert await database.admin.fetchval("SELECT hidden FROM c") == "retained"
    assert json.loads(await database.admin.fetchval("SELECT profile FROM b"))["new"] == 2


async def test_independent_actor_fk_cannot_reference_or_delete_foreign_rows(database):
    await database.admin.execute("""
      CREATE TABLE parents(id uuid PRIMARY KEY, owner_id text NOT NULL);
      CREATE TABLE children(id uuid PRIMARY KEY, owner_id text NOT NULL,
        parent_id uuid REFERENCES parents(id) ON DELETE CASCADE);
    """)
    await database.admin.execute("INSERT INTO parents VALUES($1,'user_b')", B)
    await database.admin.execute("INSERT INTO children VALUES($1,'user_a',$2)", A, B)
    contract = DataContract(
        version=1,
        tables=[
            {
                "name": "parents",
                "owner_column": "owner_id",
                "columns": [{"name": "id", "type": "uuid"}, {"name": "owner_id", "type": "text"}],
            },
            {
                "name": "children",
                "owner_column": "owner_id",
                "columns": [
                    {"name": "id", "type": "uuid"},
                    {"name": "owner_id", "type": "text"},
                    {"name": "parent_id", "type": "uuid"},
                ],
                "foreign_keys": [
                    {
                        "column": "parent_id",
                        "table": "parents",
                        "target": "id",
                        "on_delete": "CASCADE",
                    }
                ],
            },
        ],
    )
    await database.admin.execute(
        database_policy_sql(
            contract, epoch=7, project_id=PROJECT, token_secret=TOKEN_SECRET, password=PASSWORD
        )
    )
    await database.actor(actor_token())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("INSERT INTO children VALUES($1,'user_a',$2)", NEW, B)
    await database.actor(actor_token("user_b"))
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await database.runtime.execute("DELETE FROM parents WHERE id=$1", B)
    assert await database.admin.fetchval("SELECT count(*) FROM children") == 1


@pytest.mark.parametrize(
    "behavior", ["default", "generated", "domain", "domain_array", "partition", "rule", "event"]
)
async def test_actual_catalog_blocks_unmodeled_execution_and_relations(database, behavior):
    from omnia_orchestrator.services.restoration_catalog import CATALOG_SQL, contract_from_catalog

    statements = {
        "default": "CREATE FUNCTION public.dangerous_default() RETURNS text LANGUAGE plpgsql "
        "SECURITY DEFINER AS $$BEGIN DELETE FROM contacts; RETURN 'unsafe'; END$$; "
        "ALTER TABLE contacts ALTER COLUMN name SET DEFAULT public.dangerous_default()",
        "generated": "ALTER TABLE contacts ADD COLUMN generated_name text "
        "GENERATED ALWAYS AS (upper(name)) STORED",
        "domain": "CREATE DOMAIN public.custom_text AS text; "
        "ALTER TABLE contacts ADD COLUMN special public.custom_text",
        "domain_array": "CREATE DOMAIN public.custom_text AS text; "
        "ALTER TABLE contacts ADD COLUMN special public.custom_text[]",
        "partition": "CREATE TABLE partitioned(id uuid, owner_id text) PARTITION BY HASH(id)",
        "rule": "CREATE RULE no_delete AS ON DELETE TO contacts DO INSTEAD NOTHING",
        "event": "CREATE FUNCTION public.event_hook() RETURNS event_trigger LANGUAGE plpgsql "
        "AS $$BEGIN RETURN; END$$; CREATE EVENT TRIGGER fixture_event "
        "ON ddl_command_start EXECUTE FUNCTION public.event_hook()",
    }
    await database.admin.execute(statements[behavior])
    old, _ = contracts()
    _, blockers = contract_from_catalog(json.loads(await database.admin.fetchval(CATALOG_SQL)), old)
    assert blockers
    assert await database.admin.fetchval("SELECT count(*) FROM contacts") == 3


async def test_actual_nullable_fk_cycle_is_rejected_before_rls_installation(database):
    from omnia_orchestrator.services.restoration_catalog import CATALOG_SQL, contract_from_catalog

    await database.admin.execute("""
      CREATE TABLE cycle_a(id uuid PRIMARY KEY, owner_id text NOT NULL, parent_id uuid);
      CREATE TABLE cycle_b(id uuid PRIMARY KEY, owner_id text NOT NULL,
        parent_id uuid REFERENCES cycle_a(id));
      ALTER TABLE cycle_a ADD FOREIGN KEY(parent_id) REFERENCES cycle_b(id);
    """)
    old, _ = contracts()
    actual, blockers = contract_from_catalog(
        json.loads(await database.admin.fetchval(CATALOG_SQL)), old
    )
    assert "foreign_key_cycle:cycle_a" in blockers
    assert "foreign_key_cycle:cycle_b" in blockers
    with pytest.raises(ValueError, match="cycle"):
        database_policy_sql(
            actual, epoch=8, project_id=PROJECT, token_secret=TOKEN_SECRET, password=PASSWORD
        )
    assert not await database.admin.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid='public.cycle_a'::regclass"
    )


async def test_nested_known_json_cannot_be_inserted_or_replaced(database):
    await database.actor(actor_token())
    with pytest.raises(asyncpg.RaiseError, match="Nested JSON"):
        await database.runtime.execute(
            "UPDATE contacts SET metadata=$1::jsonb WHERE id=$2", '{"note":{"hidden":"value"}}', A
        )
    await database.admin.execute("ALTER TABLE contacts DISABLE TRIGGER USER")
    await database.admin.execute(
        "UPDATE contacts SET metadata=$1::jsonb WHERE id=$2", '{"note":{"old":1,"new":2}}', A
    )
    await database.admin.execute("ALTER TABLE contacts ENABLE TRIGGER USER")
    with pytest.raises(asyncpg.RaiseError, match="Nested JSON"):
        await database.runtime.execute("UPDATE contacts SET metadata='{}'::jsonb WHERE id=$1", A)
    assert json.loads(
        await database.admin.fetchval("SELECT metadata FROM contacts WHERE id=$1", A)
    ) == {"note": {"old": 1, "new": 2}}
