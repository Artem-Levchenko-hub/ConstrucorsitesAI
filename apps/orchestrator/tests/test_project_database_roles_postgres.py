"""Opt-in physical role checks on a strictly named local disposable PG16 container."""

import importlib
import os
import re
import subprocess
from uuid import uuid4

import pytest

DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
RUNTIME_PASSWORD = "runtime-synthetic-20261002-'\\-secure"
MIGRATOR_PASSWORD = "migrator-synthetic-20261002-secure"


@pytest.fixture
def database():
    container = os.environ.get("OMNIA_PROJECT_ROLES_TEST_CONTAINER", "")
    if not container:
        pytest.skip("requires owned disposable PostgreSQL container")
    assert re.fullmatch(r"omnia-project-roles-test-[a-z0-9-]+", container)
    env = dict(os.environ)
    for key in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        env.pop(key, None)
    name = "project_role_test_" + uuid4().hex

    def query(sql, *, role="postgres", password=None, db=name):
        command = [*DOCKER, "exec", "-u", "postgres", "-i"]
        if password:
            command += ["-e", "PGPASSWORD=" + password]
        command += [
            container,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "VERBOSITY=sqlstate",
            "-U",
            role,
            "-d",
            db,
        ]
        if password:
            command += ["-h", "127.0.0.1"]
        return subprocess.run(
            command, input=sql, text=True, capture_output=True, timeout=15, env=env, check=False
        )

    assert query(f'CREATE DATABASE "{name}";', db="postgres").returncode == 0
    try:
        assert (
            query(
                "CREATE TABLE public.business(id serial PRIMARY KEY, value text);"
                "INSERT INTO public.business(value) VALUES ('retained');"
                "CREATE TABLE public.__omnia_project_migrations(name text PRIMARY KEY);"
            ).returncode
            == 0
        )
        yield query
    finally:
        assert query(f'DROP DATABASE "{name}" WITH (FORCE);', db="postgres").returncode == 0


def module():
    spec = importlib.util.find_spec("yleum_orchestrator.services.project_database_roles")
    assert spec is not None, "project runtime/admin role separation is missing"
    return importlib.import_module(spec.name)


def bootstrap(query):
    sql = module().bootstrap_project_roles_sql(RUNTIME_PASSWORD, MIGRATOR_PASSWORD)
    result = query(sql)
    assert result.returncode == 0, result.stderr


def test_real_runtime_crud_admin_denials_migration_defaults_and_idempotence(database):
    q = database
    bootstrap(q)
    bootstrap(q)
    runtime = dict(role="omnia_project_runtime", password=RUNTIME_PASSWORD)
    migrator = dict(role="omnia_project_migrator", password=MIGRATOR_PASSWORD)
    assert q(
        "SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=current_user",
        **runtime,
    ).stdout.strip() == ("omnia_project_runtime|f|f|f|f|f")
    assert q("SELECT value FROM business", **runtime).stdout.strip() == "retained"
    assert (
        q(
            "INSERT INTO business(value) VALUES('crud'); UPDATE business SET value='updated' "
            "WHERE value='crud'; DELETE FROM business WHERE value='updated';",
            **runtime,
        ).returncode
        == 0
    )
    for sql in [
        "CREATE ROLE forbidden_role",
        "ALTER ROLE postgres NOSUPERUSER",
        "SET ROLE postgres",
        "SET ROLE omnia_project_owner",
        "CREATE SCHEMA forbidden",
        "CREATE TABLE public.forbidden(id int)",
        "ALTER TABLE business ADD COLUMN nope int",
        "DROP TABLE business",
        "TRUNCATE business",
        "DELETE FROM __omnia_project_migrations",
        "COPY (SELECT 1) TO PROGRAM 'true'",
        "SELECT pg_read_file('/etc/passwd')",
    ]:
        denied = q(sql, **runtime)
        assert denied.returncode != 0 and "42501" in denied.stderr, sql
    assert (
        q("CREATE TABLE public.future(id serial PRIMARY KEY, value text)", **migrator).returncode
        == 0
    )
    assert (
        q(
            "CREATE SCHEMA own; CREATE TABLE own.future(id serial PRIMARY KEY, value text)",
            **migrator,
        ).returncode
        == 0
    )
    bootstrap(q)
    assert (
        q(
            "INSERT INTO future(value) VALUES('new'); SELECT value FROM future", **runtime
        ).stdout.strip()
        == "new"
    )
    assert (
        q(
            "INSERT INTO own.future(value) VALUES('custom'); SELECT value FROM own.future",
            **runtime,
        ).stdout.strip()
        == "custom"
    )
    for sql in [
        "ALTER ROLE postgres NOSUPERUSER",
        "CREATE ROLE forbidden_migration_role",
        "SET ROLE postgres",
        "COPY (SELECT 1) TO PROGRAM 'true'",
    ]:
        denied = q(sql, **migrator)
        assert denied.returncode != 0 and "42501" in denied.stderr, sql
    assert q("SELECT value FROM business", **runtime).stdout.strip() == "retained"


def test_security_definer_fails_closed_without_partial_roles_or_business_mutation(database):
    q = database
    assert (
        q(
            "CREATE FUNCTION public.danger() RETURNS integer LANGUAGE sql SECURITY DEFINER "
            "AS 'SELECT 1';"
        ).returncode
        == 0
    )
    result = q(module().bootstrap_project_roles_sql(RUNTIME_PASSWORD, MIGRATOR_PASSWORD))
    assert result.returncode != 0 and "42501" in result.stderr
    assert q("SELECT value FROM business").stdout.strip() == "retained"
    assert (
        q(
            "SELECT proowner='postgres'::regrole FROM pg_proc "
            "WHERE oid='public.danger()'::regprocedure"
        ).stdout.strip()
        == "t"
    )


def test_extension_objects_are_not_reowned(database):
    q = database
    assert q("CREATE EXTENSION pgcrypto").returncode == 0
    bootstrap(q)
    assert (
        q(
            "SELECT proowner='postgres'::regrole FROM pg_proc "
            "WHERE oid='public.gen_random_uuid()'::regprocedure"
        ).stdout.strip()
        == "t"
    )


def test_identity_sequence_enum_and_view_keep_schema_and_data(database):
    q = database
    assert (
        q(
            "CREATE TYPE public.status AS ENUM ('ready'); "
            "CREATE TABLE public.identity_business(id bigint GENERATED ALWAYS AS IDENTITY, "
            "status public.status NOT NULL DEFAULT 'ready'); "
            "CREATE VIEW public.identity_view AS SELECT * FROM identity_business; "
            "INSERT INTO identity_business DEFAULT VALUES;"
        ).returncode
        == 0
    )
    before = q("SELECT id,status FROM identity_business").stdout
    bootstrap(q)
    assert q("SELECT id,status FROM identity_business").stdout == before
    runtime = dict(role="omnia_project_runtime", password=RUNTIME_PASSWORD)
    assert q("INSERT INTO identity_business DEFAULT VALUES", **runtime).returncode == 0
    assert q("SELECT count(*) FROM identity_view", **runtime).stdout.strip() == "2"


def test_memberships_column_acl_and_new_function_execute_are_reconciled(database):
    q = database
    bootstrap(q)
    assert (
        q(
            "GRANT pg_read_server_files TO omnia_project_runtime; "
            "GRANT UPDATE(name) ON __omnia_project_migrations TO omnia_project_runtime; "
            "GRANT CREATE ON SCHEMA public TO omnia_project_runtime;"
        ).returncode
        == 0
    )
    bootstrap(q)
    runtime = dict(role="omnia_project_runtime", password=RUNTIME_PASSWORD)
    migrator = dict(role="omnia_project_migrator", password=MIGRATOR_PASSWORD)
    for sql in [
        "SET ROLE pg_read_server_files",
        "UPDATE __omnia_project_migrations SET name='bad'",
        "CREATE TABLE forbidden(id int)",
    ]:
        denied = q(sql, **runtime)
        assert denied.returncode != 0 and "42501" in denied.stderr
    assert (
        q(
            "CREATE FUNCTION public.helper() RETURNS integer LANGUAGE sql AS 'SELECT 1'", **migrator
        ).returncode
        == 0
    )
    denied = q("SELECT public.helper()", **runtime)
    assert denied.returncode != 0 and "42501" in denied.stderr


def test_catalog_password_rekey_and_physical_ordered_hba(database):
    q = database
    new_admin = "new-admin-synthetic-20261002-secure"
    assert (
        q(
            "SET password_encryption='md5'; "
            + module().bootstrap_project_roles_sql(
                RUNTIME_PASSWORD,
                MIGRATOR_PASSWORD,
                admin_password=new_admin,
            )
        ).returncode
        == 0
    )
    container = os.environ["OMNIA_PROJECT_ROLES_TEST_CONTAINER"]
    env = dict(os.environ)
    for key in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        env.pop(key, None)
    command = [*DOCKER, "exec", "-u", "postgres", "-i", container, "sh", "-ec"]
    original = subprocess.run(
        [*command, 'cat "$PGDATA/pg_hba.conf"'], text=True, capture_output=True, check=True, env=env
    ).stdout

    def install(text):
        result = subprocess.run(
            [*command, 'cat > "$PGDATA/pg_hba.conf"'],
            input=text,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert q("SELECT pg_reload_conf()").returncode == 0

    try:
        install(
            "local all postgres peer\nhost all postgres 127.0.0.1/32 scram-sha-256\n"
            "host all all 0.0.0.0/0 reject\n"
        )
        assert q("SELECT 1", password=new_admin).returncode == 0
        assert q("SELECT 1", password="wrong-synthetic-admin-20261002").returncode != 0
        install(module().project_role_hba())
        # These connections use the real production DB name, as required by HBA.
        assert (
            q(
                "SELECT 1", db="postgres", role="omnia_project_runtime", password=RUNTIME_PASSWORD
            ).returncode
            == 0
        )
        assert (
            q(
                "SELECT 1", db="postgres", role="omnia_project_migrator", password=MIGRATOR_PASSWORD
            ).returncode
            == 0
        )
        assert q("SELECT 1", db="postgres", password=new_admin).returncode != 0
        assert q("SELECT 1", db="postgres").returncode == 0
        assert (
            q("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").stdout.strip()
            == "0"
        )
    finally:
        install(original)


@pytest.mark.parametrize("grantee", ["omnia_project_runtime", "PUBLIC"])
def test_dangerous_catalog_execute_fails_closed_for_direct_and_public_acl(database, grantee):
    q = database
    bootstrap(q)
    assert (
        q(f"GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) TO {grantee}").returncode == 0
    )
    result = q(module().bootstrap_project_roles_sql(RUNTIME_PASSWORD, MIGRATOR_PASSWORD))
    assert result.returncode != 0 and "42501" in result.stderr
    assert q("SELECT value FROM business").stdout.strip() == "retained"
    # Never read or print file contents. Bootstrap failure must stop app activation.


@pytest.mark.parametrize(
    "grant_kind", ["direct", "public", "column", "public_column", "default", "global_default"]
)
def test_unhandled_legacy_schema_acl_fails_closed_without_ownership_takeover(database, grant_kind):
    q = database
    bootstrap(q)
    owner = "legacy_owner_" + uuid4().hex
    assert (
        q(
            f"CREATE ROLE {owner} NOLOGIN; CREATE SCHEMA legacy AUTHORIZATION {owner}; "
            f"CREATE TABLE legacy.old(id int); ALTER TABLE legacy.old OWNER TO {owner}; "
            "INSERT INTO legacy.old VALUES(17);"
        ).returncode
        == 0
    )
    try:
        grants = {
            "direct": "GRANT USAGE,CREATE ON SCHEMA legacy TO omnia_project_runtime; "
            "GRANT TRUNCATE ON legacy.old TO omnia_project_runtime;",
            "public": "GRANT USAGE,CREATE ON SCHEMA legacy TO PUBLIC; "
            "GRANT TRUNCATE ON legacy.old TO PUBLIC;",
            "column": "GRANT UPDATE(id) ON legacy.old TO omnia_project_runtime;",
            "public_column": "GRANT UPDATE(id) ON legacy.old TO PUBLIC;",
            "default": f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA legacy "
            "GRANT ALL ON TABLES TO PUBLIC;",
            "global_default": f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} "
            "GRANT ALL ON TABLES TO PUBLIC;",
        }
        assert q(grants[grant_kind]).returncode == 0
        result = q(module().bootstrap_project_roles_sql(RUNTIME_PASSWORD, MIGRATOR_PASSWORD))
        assert result.returncode != 0 and "42501" in result.stderr
        assert (
            q(
                f"SELECT nspowner='{owner}'::regrole FROM pg_namespace WHERE nspname='legacy'"
            ).stdout.strip()
            == "t"
        )
        assert q("SELECT id FROM legacy.old").stdout.strip() == "17"
    finally:
        assert (
            q(
                f"REASSIGN OWNED BY {owner} TO postgres; DROP OWNED BY {owner}; DROP ROLE {owner}"
            ).returncode
            == 0
        )


def test_md5_session_cannot_downgrade_scram_credentials(database):
    q = database
    result = q(
        "SET password_encryption='md5'; "
        + module().bootstrap_project_roles_sql(
            RUNTIME_PASSWORD,
            MIGRATOR_PASSWORD,
            admin_password="review-admin-synthetic-20261002",
        )
    )
    assert result.returncode == 0
    assert (
        q(
            "SELECT bool_and(rolpassword LIKE 'SCRAM-SHA-256$%') FROM pg_authid "
            "WHERE rolname IN ('postgres','omnia_project_runtime','omnia_project_migrator')"
        ).stdout.strip()
        == "t"
    )


def test_similar_business_prefix_keeps_crud_but_real_technical_prefix_does_not(database):
    q = database
    assert (
        q(
            "CREATE TABLE public.xxomnia_business(id int); "
            "CREATE TABLE public.__omnia_technical(id int)"
        ).returncode
        == 0
    )
    bootstrap(q)
    runtime = dict(role="omnia_project_runtime", password=RUNTIME_PASSWORD)
    assert q("INSERT INTO xxomnia_business VALUES(1)", **runtime).returncode == 0
    denied = q("INSERT INTO __omnia_technical VALUES(1)", **runtime)
    assert denied.returncode != 0 and "42501" in denied.stderr
