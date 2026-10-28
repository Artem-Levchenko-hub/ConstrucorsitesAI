"""Explicit CI-only, new network-none tmpfs PG16; never attaches existing data."""

import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from yleum_orchestrator.services.publication_migration_backup import (
    RestoreSandbox,
    pack_backup,
    restore_backup,
)
from yleum_orchestrator.services.publication_migration_pg import (
    MAX_DUMP,
    read_catalog,
    row_proofs,
)


def configure_isolated_settings(monkeypatch):
    """Configure the real dispatch guard for an isolated, inventory-free CI process."""
    from yleum_orchestrator.core.config import Settings
    from yleum_orchestrator.services import generation_reader_inventory

    settings = Settings(
        _env_file=None,
        _env_prefix="QA_MIGRATION_FIXTURE_",
        database_url="postgresql://migration-ci@127.0.0.1:1/migration_fixture_unused",
        internal_token="nonsecret-isolated-migration-ci-token",
        generation_recovery_inventory_dir="",
    )
    # The DSN is configuration only: all actual SQL uses owned Docker exec.
    endpoint = urlsplit(settings.database_url)
    assert endpoint.hostname == "127.0.0.1" and endpoint.port == 1
    assert endpoint.path == "/migration_fixture_unused"
    monkeypatch.setattr(generation_reader_inventory, "get_settings", lambda: settings)
    return settings


def startup_diagnostic_codes(output):
    """Return fixed classifications only; never expose arbitrary PostgreSQL text."""
    patterns = {
        "permission_denied": b"permission denied",
        "read_only_filesystem": b"read-only file system",
        "no_space": b"no space left",
        "cannot_allocate_memory": b"cannot allocate memory",
        "invalid_configuration": b"unrecognized configuration parameter",
        "invalid_parameter_value": b"invalid value for parameter",
        "socket_missing": b"no such file or directory",
        "connection_refused": b"connection refused",
        "role_missing": b'role "postgres" does not exist',
        "timeout_usage": b"usage: timeout",
        "ready": b"database system is ready to accept connections",
    }
    lowered = output.lower()
    return sorted(name for name, pattern in patterns.items() if pattern in lowered)


def diagnose_first_empty_sandbox(monkeypatch, client):
    """Keep bounded startup evidence for only the first, still-empty QA container."""
    import docker

    from yleum_orchestrator.services.publication_migration_backup import DockerPG
    from yleum_orchestrator.services.publication_migration_pg import psql_command

    collection_type = type(client.containers)
    create = collection_type.create
    cleanup = RestoreSandbox.__exit__
    sql = DockerPG.sql
    owned = {}

    def probe_sql(pg, statement, **kwargs):
        try:
            return sql(pg, statement, **kwargs)
        except Exception as exc:
            if pg.container.id == owned.get("id") and statement == "SELECT 1;":
                owned["probe_error_type"] = type(exc).__name__
                code = str(exc)
                if re.fullmatch(r"migration_[a-z_]{1,80}", code):
                    owned["probe_error_code"] = code
            raise

    def create_empty(collection, *args, **kwargs):
        if collection.client is not client:
            return create(collection, *args, **kwargs)
        if not owned:
            assert kwargs["labels"]["omnia.resource_kind"] == "publication-migration-verifier"
            kwargs["log_config"] = docker.types.LogConfig(
                type="json-file", config={"max-size": "64k", "max-file": "1"}
            )
            # Retain crash diagnostics until the normal exact-owned cleanup below.
            kwargs["auto_remove"] = False
        container = create(collection, *args, **kwargs)
        if not owned:
            owned["id"] = container.id
        return container

    def cleanup_with_diagnostic(sandbox, *args):
        report = None
        # Production calls __exit__() without args only on failed __enter__.
        # Normal context exits (including after rows exist) have three args.
        if not args and sandbox.container is not None and sandbox.container.id == owned.get("id"):
            report = {"stage": "empty_sandbox_startup"}
            report.update(
                {
                    key: owned[key]
                    for key in ("probe_error_type", "probe_error_code")
                    if key in owned
                }
            )
            try:
                container = client.containers.get(owned["id"])
                assert container.attrs["Config"]["Labels"] == sandbox.labels
                state = container.attrs["State"]
                report.update(
                    running=state.get("Running") is True,
                    oom_killed=state.get("OOMKilled") is True,
                    exit_code=int(state.get("ExitCode", -1)),
                )
                report["startup_codes"] = startup_diagnostic_codes(
                    container.logs(tail=200)[-65536:]
                )
                if state.get("Running") is True:
                    argv = [*psql_command(seconds=5), "-c", "SELECT 1;"]
                    outcome = container.exec_run(argv, user="postgres", demux=True)
                    stdout, stderr = outcome.output
                    report["direct_exec_exit_code"] = int(outcome.exit_code)
                    report["direct_select_one"] = (stdout or b"").strip() == b"1"
                    report["direct_error_codes"] = startup_diagnostic_codes(stderr or b"")
                    report["sqlstates"] = sorted(
                        set(
                            item.decode("ascii")
                            for item in re.findall(
                                rb"(?m)^(?:ERROR|FATAL):\s+([0-9A-Z]{5})\s*$", stderr or b""
                            )
                        )
                    )
            except Exception as exc:
                report["diagnostic_error_type"] = type(exc).__name__
        try:
            cleanup(sandbox, *args)
        finally:
            if report is not None and sandbox.cleaned:
                report["cleanup_verified"] = True
                pytest.fail(json.dumps(report, sort_keys=True), pytrace=False)

    monkeypatch.setattr(collection_type, "create", create_empty)
    monkeypatch.setattr(DockerPG, "sql", probe_sql)
    monkeypatch.setattr(RestoreSandbox, "__exit__", cleanup_with_diagnostic)


def test_startup_diagnostics_never_return_arbitrary_log_text():
    private = b"untrusted arbitrary payload must stay private"
    assert startup_diagnostic_codes(private) == []
    assert startup_diagnostic_codes(private + b" Permission denied") == ["permission_denied"]


def test_isolated_dispatch_settings_ignore_dotenv_and_keep_real_guard(monkeypatch, tmp_path):
    from yleum_orchestrator.services import generation_reader_inventory

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=invalid\nINTERNAL_TOKEN=invalid\n")
    before = generation_reader_inventory.require_if_configured
    settings = configure_isolated_settings(monkeypatch)
    assert generation_reader_inventory.get_settings() is settings
    assert generation_reader_inventory.require_if_configured is before
    assert generation_reader_inventory.reader_dispatch(lambda: "isolated") == "isolated"
    settings.generation_recovery_inventory_dir = str(tmp_path / "unregistered-inventory")
    monkeypatch.setattr(generation_reader_inventory, "_OWN", None)
    with pytest.raises(generation_reader_inventory.ReaderInventoryUnavailable):
        generation_reader_inventory.reader_dispatch(lambda: pytest.fail("guard was bypassed"))


@pytest.mark.skipif(
    os.environ.get("QA_MIGRATION_DOCKER_IMAGE") is None,
    reason="requires explicit disposable Docker CI image",
)
@pytest.mark.parametrize("fully_qualified_alias", [False, True])
def test_network_none_restore_proves_full_schema_and_original_rows_before_and_after_plan(
    monkeypatch, fully_qualified_alias,
):
    import docker

    from yleum_orchestrator.services.project_database_schema_proof import legacy_schema_digest
    from yleum_orchestrator.services.publication_migration_pg import (
        PUBLIC_SCHEMA_OWNER_STATEMENTS,
        MigrationIOError,
        read_public_schema_owner,
    )
    from yleum_orchestrator.services.publication_migration_schema import _lines, read_schema_dump

    assert os.environ.get("CI") == "true"
    assert (
        os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        == "unix:///var/run/docker.sock"
    )
    image = os.environ["QA_MIGRATION_DOCKER_IMAGE"]
    assert re.fullmatch(
        r"(?:(?:docker\.io|index\.docker\.io)/)?(?:library/)?postgres@sha256:[0-9a-f]{64}", image
    )
    restore_image = (
        "docker.io/library/postgres@" + image.split("@", 1)[1] if fully_qualified_alias else image
    )
    configure_isolated_settings(monkeypatch)
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=20)
    diagnose_first_empty_sandbox(monkeypatch, client)
    project = uuid4()
    source = RestoreSandbox(client, project, uuid4(), image)
    restored = RestoreSandbox(client, project, uuid4(), restore_image)
    try:
        with source as live:
            live.sql(
                "CREATE ROLE omnia_project_owner NOLOGIN;"
                "ALTER SCHEMA public OWNER TO omnia_project_owner;"
                "CREATE TABLE public.orders(id bigint PRIMARY KEY, amount integer NOT NULL);"
                "INSERT INTO public.orders VALUES(1,630),(2,650);"
                "CREATE TABLE public.__omnia_project_migrations(name text PRIMARY KEY,"
                "sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now());"
            )
            before = live.full_schema()
            before_dump = read_schema_dump(live)
            assert read_public_schema_owner(live) == "omnia_project_owner"
            tables = read_catalog(live)
            rows = row_proofs(live, tables)
            packed = pack_backup(
                live.command(
                    [
                        "pg_dumpall",
                        "--globals-only",
                        "--no-role-passwords",
                        "-h",
                        "/tmp",
                        "-U",
                        "postgres",
                    ]
                ),
                live.command(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        "-h",
                        "/tmp",
                        "-U",
                        "postgres",
                        "-d",
                        "postgres",
                    ],
                    max_bytes=MAX_DUMP,
                ),
            )
            plan = (
                "ALTER TABLE public.orders ADD COLUMN pickup text;"
                "ALTER TABLE public.orders ADD COLUMN comment text;"
                "ALTER TABLE public.orders ADD COLUMN pickup_at timestamptz;"
            )
            live.sql(plan)
            after = live.full_schema()
            with restored as verify:
                assert restored.container.image.id == source.container.image.id
                assert restored.container.attrs["Config"]["Image"] == restore_image
                # Observe the exact old no-owner restore before the real fix's
                # one public-owner statement. Do not repeat restore or fabricate proof.
                before_owner = []
                original_sql = verify.sql

                def observe_owner_restore(sql):
                    if sql == PUBLIC_SCHEMA_OWNER_STATEMENTS["omnia_project_owner"]:
                        assert read_public_schema_owner(verify) == "pg_database_owner"
                        before_owner.append(read_schema_dump(verify))
                    return original_sql(sql)

                with monkeypatch.context() as observer:
                    observer.setattr(verify, "sql", observe_owner_restore)
                    restore_backup(verify, packed, public_schema_owner="omnia_project_owner")
                assert len(before_owner) == 1
                assert legacy_schema_digest(before_owner[0]) != before
                assert len(_lines(before_dump)) - len(_lines(before_owner[0])) == 3
                assert [s for s in _lines(before_dump) if s.strip()] == [
                    s for s in _lines(before_owner[0]) if s.strip()
                ]
                assert read_public_schema_owner(verify) == "omnia_project_owner"
                assert verify.full_schema() == before
                assert row_proofs(verify, tables) == rows
                verify.sql(plan)
                assert verify.full_schema() == after
                assert row_proofs(verify, tables) == rows
                verify.sql("INSERT INTO public.orders(id,amount,pickup) VALUES(3,270,'now');")
                assert verify.json("SELECT count(*) FROM public.orders;") == 3
                verify.sql("ALTER DATABASE postgres OWNER TO omnia_project_owner;")
                with pytest.raises(
                    MigrationIOError, match="migration_public_schema_owner_unsupported",
                ):
                    read_public_schema_owner(verify)
        assert source.cleaned and restored.cleaned
    finally:
        client.close()


@pytest.mark.skipif(
    os.environ.get("QA_MIGRATION_DOCKER_IMAGE") is None,
    reason="requires explicit disposable Docker CI image",
)
def test_catalog_observers_never_execute_public_aggregate_overloads(monkeypatch):
    import docker

    from yleum_orchestrator.services.publication_migration_pg import (
        MigrationIOError,
        pg_identity,
        psql_command,
        verify_ledger,
    )
    from yleum_orchestrator.services.restoration_database import TRUSTED_ADMIN_PGOPTIONS

    assert os.environ.get("CI") == "true" and os.name == "posix"
    assert os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock") == (
        "unix:///var/run/docker.sock"
    )
    image = os.environ["QA_MIGRATION_DOCKER_IMAGE"]
    assert re.fullmatch(r"(?:(?:docker\.io/)?library/)?postgres@sha256:[0-9a-f]{64}", image)
    configure_isolated_settings(monkeypatch)
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=20)
    diagnose_first_empty_sandbox(monkeypatch, client)
    sandbox = RestoreSandbox(client, uuid4(), uuid4(), image)
    try:
        assert client.info()["OSType"] == "linux"
        with sandbox as pg:
            pg.sql(
                "CREATE SEQUENCE public.observer_invocations;"
                "CREATE TABLE public.orders(id bigint PRIMARY KEY, amount integer NOT NULL);"
                "CREATE TABLE public.__omnia_project_migrations(name text PRIMARY KEY,"
                "sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now());"
            )
            for sql_type in ("name", "json", "text"):
                pg.sql(
                    f"CREATE FUNCTION public.spy_{sql_type}(acc bigint,item pg_catalog.{sql_type}) "
                    "RETURNS bigint LANGUAGE plpgsql AS $body$ BEGIN "
                    "PERFORM pg_catalog.nextval("
                    "'public.observer_invocations'::pg_catalog.regclass);"
                    "RETURN acc+1; END; $body$;"
                    f"CREATE AGGREGATE public.json_agg(pg_catalog.{sql_type}) "
                    f"(SFUNC=public.spy_{sql_type},STYPE=pg_catalog.int8,INITCOND='0');"
                )

            def untouched():
                return (
                    pg.json(
                        "SELECT pg_catalog.to_json(NOT is_called) FROM public.observer_invocations;"
                    )
                    is True
                )

            assert pg_identity(pg)["pg_version"] == "16"
            assert verify_ledger(pg, required=True)
            assert [table.name for table in read_catalog(pg)] == ["orders"]
            assert untouched(), "strict observers executed an untrusted transition function"

            class UnsafeOldOptions:
                def json(self, sql):
                    # Explicit old-startup control only inside this fresh nonce-owned DB.
                    # The nested env overrides the safe wrapper for this one QA command.
                    output = pg.command(
                        ["env", "PGOPTIONS=" + TRUSTED_ADMIN_PGOPTIONS, *psql_command()[6:]],
                        data=sql.encode(),
                    )
                    return json.loads(output)

            with pytest.raises(MigrationIOError):
                pg_identity(UnsafeOldOptions())
            assert not untouched(), "unsafe control did not actually execute the public overload"
            pg.sql("ALTER SEQUENCE public.observer_invocations RESTART WITH 1;")
            assert untouched()
            assert pg_identity(pg)["pg_version"] == "16"
            assert verify_ledger(pg, required=True)
            assert [table.name for table in read_catalog(pg)] == ["orders"]
            assert untouched(), "fixed observers executed the public overload after control"
        assert sandbox.cleaned
    finally:
        client.close()
