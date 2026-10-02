"""Restore helpers preserve peer admin and reconcile before generated writers."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_project_database_docker_lifecycle_postgres import (
    database as physical_database,  # noqa: F401 -- opt-in disposable fixture
)
from tests.test_project_database_docker_lifecycle_postgres import (
    query,
)
from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services import code_restoration_engine as module
from yleum_orchestrator.services.code_restoration_engine import (
    CodeRestorationEngine,
    PreparationNeedsChanges,
)


@pytest.mark.parametrize("identities", [None, ["public.max_users"]])
def test_dump_authenticates_as_postgres_os_peer(monkeypatch, identities):
    api = SimpleNamespace(
        exec_create=Mock(return_value={"Id": "dump"}),
        exec_start=Mock(return_value=SimpleNamespace(_sock=Mock(), close=Mock())),
        exec_inspect=Mock(return_value={"Running": False, "ExitCode": 0}),
    )
    backend = SimpleNamespace(
        client=SimpleNamespace(api=api), _project_postgres=lambda: SimpleNamespace(id="database")
    )
    monkeypatch.setattr(module, "read_controller_output", lambda *_args, **_kwargs: b"copy")
    if identities is None:
        assert CodeRestorationEngine._dump(backend) == b"copy"
    else:
        assert CodeRestorationEngine._dump_identity_rows(backend, identities) == b"copy"
    _args, kwargs = api.exec_create.call_args
    assert kwargs.get("user") == "postgres"
    assert "PGPASSWORD" not in kwargs["environment"]
    assert kwargs["environment"]["PGOPTIONS"].startswith("-c search_path=public")


def test_empty_sql_inventory_takes_priority_over_generated_runner():
    files = {
        "scripts/apply-migrations.mjs": "untrusted JavaScript",
        "src/lib/db/schema.ts": "export const tasks = pgTable('tasks', {});",
        "drizzle/0002_tasks.sql": "CREATE TABLE tasks(id integer);",
    }
    assert module.empty_project_sql_inventory(files) == [
        ("drizzle/0002_tasks.sql", "CREATE TABLE tasks(id integer);")
    ]


def test_js_only_empty_schema_requires_explicit_adaptation(monkeypatch):
    monkeypatch.setattr(module, "empty_database_materializer", lambda _: ["node", "runner"])
    with pytest.raises(PreparationNeedsChanges, match=r"SQL.*адаптац"):
        module.empty_project_sql_inventory({"scripts/apply-migrations.mjs": "known runner"})


def test_materializer_uses_limited_login_and_reconciles(monkeypatch):
    events = []
    backend = SimpleNamespace(
        prepare_project_database_migrations=lambda epoch: events.append(("prepare", epoch)),
        bootstrap_project_database_roles=lambda: events.append("reconcile"),
    )
    monkeypatch.setattr(module, "migrator_sql", lambda owner, sql: events.append(("migrator", sql)))
    monkeypatch.setattr(module, "admin_sql", lambda *_args, **_kwargs: pytest.fail("admin SQL"))
    CodeRestorationEngine._materialize_empty_project_database(
        backend, [("drizzle/0002.sql", "CREATE TABLE tasks(id integer);")], epoch=1
    )
    assert events[0] == ("prepare", 1)
    assert events[-1] == "reconcile"
    assert events[1][0] == "migrator"
    source = "CREATE TABLE tasks(id integer);"
    assert source not in events[1][1]
    assert source.encode().hex() in events[1][1]


@pytest.mark.parametrize("source", [r"\connect postgres", r"\! test-marker", "invalid SQL '"])
def test_historical_source_never_reaches_psql_as_meta_commands(monkeypatch, source):
    # Capture only; these malformed inputs are never sent to a database/shell.
    backend = SimpleNamespace(
        prepare_project_database_migrations=Mock(), bootstrap_project_database_roles=Mock()
    )
    captured = []
    monkeypatch.setattr(module, "migrator_sql", lambda _backend, text: captured.append(text))
    CodeRestorationEngine._materialize_empty_project_database(
        backend,
        [("historical.sql", source)],
        epoch=1,
    )
    expected = (
        "DO $omnia_restore$ BEGIN EXECUTE pg_catalog.convert_from("
        "pg_catalog.decode('" + source.encode().hex() + "', 'hex'), 'UTF8'); "
        "END $omnia_restore$;"
    )
    assert captured == [expected]
    assert "\\" not in captured[0]
    assert source not in captured[0]


def test_failed_migration_does_not_reconcile_or_start_other_work(monkeypatch):
    backend = SimpleNamespace(
        prepare_project_database_migrations=Mock(), bootstrap_project_database_roles=Mock()
    )

    def fail(*_args):
        raise CellResourceError("migration denied")

    monkeypatch.setattr(module, "migrator_sql", fail)
    with pytest.raises(CellResourceError, match="migration denied"):
        CodeRestorationEngine._materialize_empty_project_database(
            backend, [("one.sql", "bad SQL"), ("two.sql", "other")], epoch=1
        )
    backend.bootstrap_project_database_roles.assert_not_called()


def test_logical_import_reconciles_before_return(monkeypatch):
    events = []
    backend = SimpleNamespace(
        prepare_project_database_migrations=lambda epoch: events.append(("prepare", epoch)),
        bootstrap_project_database_roles=lambda: events.append("reconcile"),
    )
    monkeypatch.setattr(
        module, "admin_sql", lambda owner, sql, **kwargs: events.append(("import", sql))
    )
    CodeRestorationEngine._restore_project_database(backend, b"COPY tasks;", epoch=1)
    assert events == [("prepare", 1), ("import", "COPY tasks;"), "reconcile"]


def test_identity_import_reconciles_restored_acl(monkeypatch):
    events = []
    backend = SimpleNamespace(bootstrap_project_database_roles=lambda: events.append("reconcile"))
    monkeypatch.setattr(module, "admin_sql", lambda *_args: events.append("identity import"))
    CodeRestorationEngine._install_identity_rows(backend, ["public.max_users"], b"COPY max_users;")
    assert events == ["identity import", "reconcile"]


async def test_failed_reconciliation_prevents_any_generated_service_start():
    def fail():
        raise CellResourceError("role reconciliation denied")

    backend = SimpleNamespace(bootstrap_project_database_roles=fail, start_service=Mock())
    manifest = SimpleNamespace(
        service_order=lambda: ["web"], services=[SimpleNamespace(name="web")]
    )
    with pytest.raises(CellResourceError, match="role reconciliation denied"):
        await CodeRestorationEngine._start(backend, manifest, 1)
    backend.start_service.assert_not_called()


def test_capture_reconciliation_never_accepts_source_password(monkeypatch):
    backend = SimpleNamespace(bootstrap_project_database_roles=Mock())
    monkeypatch.setattr(
        module, "admin_sql", lambda *_args, **_kwargs: pytest.fail("password ALTER")
    )
    CodeRestorationEngine._reconcile_candidate_database(backend)
    backend.bootstrap_project_database_roles.assert_called_once_with()


def test_real_sql_restore_limited_materialization_and_reconciled_crud(physical_database):  # noqa: F811
    """Real PG SQL path; whole-product quiescence is covered by backend tests."""
    from yleum_orchestrator.services.restoration_database import admin_sql

    runtime, postgres = physical_database
    runtime.bootstrap_project_database_roles()
    events = []
    candidate = SimpleNamespace(
        client=runtime.client,
        _project_postgres=runtime._project_postgres,
        database_credentials=runtime.database_credentials,
        bootstrap_project_database_roles=runtime.bootstrap_project_database_roles,
        prepare_project_database_migrations=lambda epoch: events.append(("quiesce", epoch)),
    )
    CodeRestorationEngine._materialize_empty_project_database(
        candidate,
        [
            (
                "drizzle/0002.sql",
                "CREATE TABLE public.restore_probe("
                "id serial PRIMARY KEY,value text,actor text);"
                "INSERT INTO public.restore_probe(value,actor) "
                "SELECT 'retained',current_user || ':' || session_user;",
            )
        ],
        epoch=1,
    )
    assert query(runtime, postgres, "SELECT actor FROM restore_probe").output.strip() == (
        b"omnia_project_migrator:omnia_project_migrator"
    )
    dump = CodeRestorationEngine._dump(candidate)
    assert b"retained" in dump
    admin_sql(runtime, "DROP TABLE public.restore_probe;")
    CodeRestorationEngine._restore_project_database(candidate, dump, epoch=1)
    assert events == [("quiesce", 1), ("quiesce", 1)]
    assert query(runtime, postgres, "SELECT value FROM restore_probe").output.strip() == b"retained"
    assert (
        query(
            runtime,
            postgres,
            "INSERT INTO restore_probe(value) VALUES('crud');"
            "UPDATE restore_probe SET value='updated' WHERE value='crud';"
            "DELETE FROM restore_probe WHERE value='updated';",
        ).exit_code
        == 0
    )
    for sql in (
        "TRUNCATE restore_probe",
        "ALTER TABLE restore_probe ADD COLUMN blocked int",
        "SET ROLE postgres",
        "CREATE ROLE blocked",
    ):
        assert query(runtime, postgres, sql).exit_code != 0
    assert query(runtime, postgres, "SELECT count(*) FROM restore_probe").output.strip() == b"1"
