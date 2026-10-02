"""Opt-in lifecycle acceptance: own networkless PG16, no published ports or user data."""

import os
import time

import pytest

from tests.test_docker_machine_backend import backend
from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.project_machine import write_controller_json
from yleum_orchestrator.services.project_migrations import run_project_migrations
from yleum_orchestrator.services.restoration_database import (
    TRUSTED_ADMIN_PGOPTIONS,
    admin_sql,
    migrator_sql,
)


@pytest.fixture
def database(tmp_path):
    if os.environ.get("OMNIA_PROJECT_DOCKER_LIFECYCLE_TEST") != "1":
        pytest.skip("requires explicitly enabled owned disposable PostgreSQL")
    import docker

    client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    image = client.images.get("mirror.gcr.io/library/postgres:16-alpine").id
    runtime = backend(tmp_path, client=client, postgres_image=image, namespace="test")
    # UUID-bound labels/names belong exclusively to this fixture. No host ports,
    # external network, customer resources, or shared cluster are reachable.
    runtime._prepare_project_postgres_volume()
    options = runtime._project_postgres_options("unused", 1)
    options["network_mode"] = "none"
    postgres = client.containers.create(image, **options)
    try:
        postgres.start()
        runtime._wait_project_postgres_ready(postgres)
        yield runtime, postgres
    finally:
        postgres.remove(force=True)
        client.volumes.get(runtime.project_postgres_volume).remove()
        client.close()


def query(runtime, postgres, sql, *, role="omnia_project_runtime", password=None):
    credentials = runtime.database_credentials()
    password = credentials.runtime_password if password is None else password
    return postgres.exec_run(
        [
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-U",
            role,
            "-d",
            "postgres",
            "-c",
            sql,
        ],
        environment={"PGPASSWORD": password},
        user="postgres",
    )


def test_retained_rows_limited_migration_runtime_crud_and_authentication(database):
    runtime, postgres = database
    baseline_control = admin_sql(
        runtime,
        "SELECT EXISTS (SELECT FROM pg_proc p, "
        "LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
        "WHERE p.oid='pg_catalog.pg_control_system()'::regprocedure AND a.grantee=0 "
        "AND a.privilege_type='EXECUTE');",
    ).strip()
    seed_admin_hash = admin_sql(
        runtime, "SELECT rolpassword FROM pg_authid WHERE rolname='postgres'"
    )
    admin_sql(
        runtime,
        "CREATE TABLE retained(id serial PRIMARY KEY, value text);"
        "INSERT INTO retained(value) VALUES ('retained');",
    )
    runtime.bootstrap_project_database_roles()
    credentials = runtime.database_credentials()
    assert query(runtime, postgres, "SELECT value FROM retained").output.strip() == b"retained"
    # Adoption must run before journal creation, preserving populated historical DBs.
    with pytest.raises(CellResourceError, match="verification failed"):
        run_project_migrations(
            runtime, {"drizzle/0002.sql": "TRUNCATE retained;"}, verify_only=False
        )
    assert admin_sql(runtime, "SELECT count(*) FROM retained").strip() == b"1"
    assert (
        admin_sql(
            runtime, "SELECT to_regclass('public.__omnia_project_migrations') IS NULL"
        ).strip()
        == b"t"
    )
    # Only explicit trusted fixture reconciliation supplies a historical journal.
    admin_sql(
        runtime,
        "CREATE TABLE public.__omnia_project_migrations(name text PRIMARY KEY,"
        "sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());",
    )
    migrations = {
        "drizzle/0002.sql": "ALTER TABLE retained ADD COLUMN detail text;"
        "CREATE TABLE future(id serial PRIMARY KEY, value text);"
    }
    receipt = run_project_migrations(runtime, migrations, verify_only=False)
    assert len(receipt["database_identity"]) == 64
    assert (
        run_project_migrations(runtime, migrations, verify_only=False, verify_applied=True)[
            "catalog_digest"
        ]
        == receipt["catalog_digest"]
    )
    crud = query(
        runtime,
        postgres,
        "INSERT INTO future(value) VALUES('crud');"
        "UPDATE future SET value='updated'; DELETE FROM future;",
    )
    assert crud.exit_code == 0
    assert query(runtime, postgres, "SELECT value FROM retained").output.strip() == b"retained"
    for sql in [
        "TRUNCATE retained",
        "CREATE TABLE forbidden(id int)",
        "ALTER TABLE retained ADD COLUMN forbidden int",
        "SET ROLE postgres",
        "SET ROLE omnia_project_owner",
        "DELETE FROM __omnia_project_migrations",
        "CREATE ROLE forbidden",
        "SELECT pg_read_file('/etc/passwd')",
        "COPY (SELECT 1) TO PROGRAM 'true'",
    ]:
        assert query(runtime, postgres, sql).exit_code != 0, sql
    # Even a correct or formerly exposed administrator password cannot use TCP.
    for password in [credentials.admin_password, runtime.project_postgres_password]:
        assert (
            query(runtime, postgres, "SELECT 1", role="postgres", password=password).exit_code != 0
        )
    assert (
        query(runtime, postgres, "SELECT 1", password=credentials.migrator_password).exit_code != 0
    )
    assert (
        query(
            runtime,
            postgres,
            "SELECT 1",
            role="omnia_project_migrator",
            password=credentials.migrator_password,
        ).exit_code
        == 0
    )
    assert (
        query(
            runtime,
            postgres,
            "RESET ROLE; ALTER ROLE postgres NOSUPERUSER",
            role="omnia_project_migrator",
            password=credentials.migrator_password,
        ).exit_code
        != 0
    )
    assert (
        admin_sql(
            runtime,
            "SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname='postgres'",
        ).strip()
        == b"t"
    )
    assert (
        admin_sql(runtime, "SELECT rolpassword FROM pg_authid WHERE rolname='postgres'")
        != seed_admin_hash
    )
    assert query(runtime, postgres, "SELECT current_user,session_user").output.strip() == (
        b"omnia_project_runtime|omnia_project_runtime"
    )
    # PG16 exposes this metadata function to PUBLIC by default. Bootstrap must
    # not add privileges to obtain receipts; the receipt uses trusted peer provenance.
    control = query(
        runtime,
        postgres,
        "SELECT has_function_privilege(current_user,'pg_catalog.pg_control_system()','EXECUTE')",
    )
    assert control.output.strip() == baseline_control
    assert query(runtime, postgres, "SELECT count(*) FROM pg_control_system()").exit_code == (
        0 if baseline_control == b"t" else 1
    )
    assert postgres.attrs["HostConfig"]["NetworkMode"] == "none"
    assert not postgres.attrs["HostConfig"].get("PortBindings")


def test_fresh_migration_journal_is_private_during_user_sql(database):
    runtime, postgres = database
    receipt = run_project_migrations(
        runtime,
        {
            "drizzle/0002.sql": """
        DO $test$ BEGIN
          IF has_table_privilege('omnia_project_runtime',
               'public.__omnia_project_migrations','INSERT') THEN
            RAISE EXCEPTION 'runtime journal write privilege leaked';
          END IF;
        END $test$;
        CREATE TABLE fresh(id serial PRIMARY KEY, body text);
        """
        },
        verify_only=False,
    )
    assert len(receipt["database_identity"]) == 64
    assert query(runtime, postgres, "INSERT INTO fresh(body) VALUES ('crud')").exit_code == 0
    assert query(runtime, postgres, "DELETE FROM __omnia_project_migrations").exit_code != 0


def test_trusted_bootstrap_ignores_retained_admin_search_path_and_public_shadow(database):
    runtime, postgres = database
    # Reproduce retained settings from an earlier administrator-exposed project.
    # This deliberately hostile SQL is installed only in the owned disposable PG16.
    admin_sql(
        runtime,
        """
        CREATE TABLE public.retained_guard(value text);
        INSERT INTO public.retained_guard VALUES ('retained');
        CREATE TABLE public.shadow_witness(actor text);
        CREATE FUNCTION public.pg_reload_conf() RETURNS boolean LANGUAGE plpgsql AS
        $shadow$ BEGIN
          INSERT INTO public.shadow_witness VALUES (current_user);
          RETURN true;
        END $shadow$;
        ALTER ROLE postgres SET search_path=public,pg_catalog;
    """,
    )
    runtime.bootstrap_project_database_roles()
    assert admin_sql(runtime, "SHOW search_path;").strip() == b"public"
    assert admin_sql(runtime, "SELECT count(*) FROM public.shadow_witness").strip() == b"0"
    assert (
        query(
            runtime, postgres, "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user"
        ).output.strip()
        == b"f"
    )
    assert (
        query(runtime, postgres, "SELECT value FROM retained_guard").output.strip() == b"retained"
    )
    assert query(runtime, postgres, "ALTER ROLE postgres NOSUPERUSER").exit_code != 0


def test_migration_quiesce_kills_real_unregistered_database_writer(database):
    from tests.test_project_machine_manifest import payload

    runtime, postgres = database
    admin_sql(
        runtime,
        "CREATE TABLE public.writer_guard(ticks int); INSERT INTO public.writer_guard VALUES(0);",
    )
    runtime.bootstrap_project_database_roles()
    product = runtime.client.containers.create(
        runtime.postgres_image,
        ["trap 'exit 0' TERM; while :; do sleep 1; done"],
        entrypoint=["sh", "-c"],
        name=runtime.machine_name,
        labels={**runtime.labels("development"), "omnia.fencing_epoch": "1"},
        network_mode="container:" + postgres.id,
        user="postgres",
        detach=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        ports={},
    )
    try:
        product.start()
        execution = runtime.client.api.exec_create(
            product.id,
            [
                "sh",
                "-c",
                "while :; do psql -Xq -h 127.0.0.1 -U omnia_project_runtime "
                '-d postgres -c "UPDATE public.writer_guard SET ticks=ticks+1"; sleep 0.05; done',
            ],
            environment={"PGPASSWORD": runtime.database_credentials().runtime_password},
            user="postgres",
        )
        runtime.client.api.exec_start(execution["Id"], detach=True)
        deadline = time.monotonic() + 10
        while admin_sql(runtime, "SELECT ticks FROM public.writer_guard").strip() == b"0":
            assert time.monotonic() < deadline, "owned writer never became active"
            time.sleep(0.05)
        write_controller_json(
            runtime.metadata_path,
            {
                "manifest": payload(),
                "epoch": 1,
                "services": {},
                "exec_logs": {execution["Id"]: "writer.log"},
                "exec_pids": {execution["Id"]: "writer.pid"},
            },
        )
        assert runtime.client.api.exec_inspect(execution["Id"])["Running"]
        runtime.prepare_project_database_migrations(1)
        assert not runtime.client.api.exec_inspect(execution["Id"])["Running"]
        saved = admin_sql(runtime, "SELECT ticks FROM public.writer_guard")
        time.sleep(0.2)
        assert admin_sql(runtime, "SELECT ticks FROM public.writer_guard") == saved
        assert runtime._metadata()["services"] == {}
        assert runtime._metadata()["exec_pids"] == {}
        product.reload()
        assert product.status == "running"  # only the original idle PID1 resumed
    finally:
        product.remove(force=True)


def test_retained_admin_logging_cannot_disclose_new_synthetic_credentials(database):
    runtime, postgres = database
    credentials = runtime.database_credentials()
    admin_sql(
        runtime,
        """
        ALTER ROLE postgres SET log_statement='all';
        ALTER ROLE postgres SET session_preload_libraries='omnia_missing_test_library';
        ALTER ROLE postgres SET local_preload_libraries='omnia_missing_test_library';
        CREATE ROLE omnia_project_migrator LOGIN NOSUPERUSER;
        ALTER ROLE omnia_project_migrator SET log_statement='all';
        ALTER ROLE omnia_project_migrator
          SET session_preload_libraries='omnia_missing_test_library';
        ALTER ROLE omnia_project_migrator IN DATABASE postgres SET log_duration='on';
    """,
    )
    runtime.bootstrap_project_database_roles()
    logs = postgres.logs()
    if any(
        value.encode() in logs
        for value in (
            credentials.runtime_password,
            credentials.migrator_password,
            credentials.admin_password,
        )
    ):
        pytest.fail("trusted bootstrap disclosed synthetic credentials in owned PostgreSQL logs")
    assert admin_sql(runtime, "SHOW log_statement;").strip() == b"none"
    assert admin_sql(runtime, "SHOW session_preload_libraries;").strip() == b""
    # Bootstrap removed both global and per-DB defaults before limited login.
    assert migrator_sql(runtime, "SHOW log_statement;").strip() == b"none"
    assert migrator_sql(runtime, "SHOW log_duration;").strip() == b"off"
    assert migrator_sql(runtime, "SELECT current_user,session_user").strip() == (
        b"omnia_project_migrator|omnia_project_migrator"
    )
    # Limited sessions cannot inspect this privileged GUC, but their successful
    # connection proves the deliberately missing library was not preloaded.
    assert (
        admin_sql(
            runtime,
            "SELECT COALESCE(array_length(rolconfig,1),0) FROM pg_catalog.pg_roles "
            "WHERE rolname='omnia_project_migrator'",
        ).strip()
        == b"0"
    )
    assert (
        admin_sql(
            runtime,
            "SELECT NOT EXISTS (SELECT FROM pg_catalog.pg_db_role_setting "
            "WHERE setrole=(SELECT oid FROM pg_catalog.pg_roles "
            "WHERE rolname='omnia_project_migrator'))",
        ).strip()
        == b"t"
    )
    # SUSET administrator options cannot be copied onto a NOSUPERUSER LOGIN.
    restricted = postgres.exec_run(
        [
            "psql",
            "-XqAt",
            "-h",
            "127.0.0.1",
            "-U",
            "omnia_project_migrator",
            "-d",
            "postgres",
            "-c",
            "SELECT 1",
        ],
        environment={
            "PGPASSWORD": credentials.migrator_password,
            "PGOPTIONS": TRUSTED_ADMIN_PGOPTIONS,
        },
        user="postgres",
    )
    assert restricted.exit_code != 0
    assert b"permission denied to set parameter" in restricted.output


@pytest.mark.parametrize("verify_only", [True, False], ids=["verify_only", "empty_inventory"])
def test_generated_public_hash_function_cannot_forge_controller_catalog_receipt(
    database, verify_only
):
    runtime, postgres = database
    admin_sql(
        runtime,
        "CREATE TABLE public.receipt_guard(value text); "
        "INSERT INTO public.receipt_guard VALUES ('retained');",
    )
    baseline = run_project_migrations(runtime, {}, verify_only=verify_only)
    # The generated migrator can create ordinary project functions. Even under
    # this limited login, such a function must never compute a trusted receipt.
    migrator_sql(
        runtime,
        "CREATE FUNCTION public.sha256(bytea) RETURNS bytea "
        "LANGUAGE SQL IMMUTABLE AS $$ SELECT pg_catalog.decode("
        "pg_catalog.repeat('ab',32),'hex') $$;",
    )
    observed = run_project_migrations(runtime, {}, verify_only=verify_only)
    assert observed["catalog_digest"] != "ab" * 32
    assert observed["catalog_digest"] == baseline["catalog_digest"]
    assert observed["database_identity"] == baseline["database_identity"]
    assert query(runtime, postgres, "SELECT value FROM receipt_guard").output.strip() == b"retained"
    assert (
        admin_sql(
            runtime, "SELECT pg_catalog.to_regclass('public.__omnia_project_migrations') IS NULL"
        ).strip()
        == b"t"
    )
