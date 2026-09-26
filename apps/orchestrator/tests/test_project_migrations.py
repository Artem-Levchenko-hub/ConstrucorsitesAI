from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4, uuid5

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.project_migrations import (
    adaptation_database,
    migration_sql,
    select_migrations,
)


def test_portable_inventory_excludes_only_exact_legacy_content():
    legacy = {"drizzle/0000_max_core.sql": "legacy core"}
    files = {**legacy, "drizzle/0002_notes.sql": "CREATE TABLE qa_notes(id int);"}
    assert list(select_migrations(files, legacy)) == ["drizzle/0002_notes.sql"]
    with pytest.raises(CellResourceError, match=r"legacy migration.*ambiguous"):
        select_migrations(
            {**files, "drizzle/0000_max_core.sql": "CREATE TABLE own(id int);"}, legacy
        )


def test_migration_sql_preserves_named_schemas_and_cannot_inject_psql():
    source = 'CREATE SCHEMA own; CREATE TABLE "public".qa_notes(id int);'
    sql = migration_sql({"drizzle/0002_notes.sql": source}, verify_only=False)
    assert source.encode().hex() in sql
    assert hashlib.sha256(source.encode()).hexdigest().encode().hex() in sql
    assert "pg_advisory_xact_lock" in sql


def test_adaptation_detected_from_controller_marker_and_old_controller_operation():
    run = uuid4()
    state = SimpleNamespace(active_generation_run_id=run, operations=())
    assert adaptation_database(state, {"restoration_adaptation_run_id": str(run)})
    operation = SimpleNamespace(operation_id=uuid5(run, "restoration-adaptation-candidate"))
    state.operations = (operation,)
    assert adaptation_database(state, {})
    state.operations = ()
    assert not adaptation_database(state, {})
    assert not adaptation_database(state, {"restoration_adaptation_run_id": str(uuid4())})


def test_adaptation_query_is_read_only_and_never_checks_historical_migration_journal():
    sql = migration_sql({"drizzle/0002.sql": "DROP TABLE important;"}, verify_only=True)
    assert "BEGIN READ ONLY" in sql
    assert b"DROP TABLE important".hex() not in sql
    assert "CREATE TABLE" not in sql
    assert "__omnia_project_migrations" not in sql


def test_migration_psql_has_process_deadline_even_when_sql_disables_statement_timeout(monkeypatch):
    from yleum_orchestrator.services import restoration_database as module

    commands = []
    sock = SimpleNamespace(
        settimeout=lambda _: None, sendall=lambda _: None, shutdown=lambda _: None
    )
    connection = SimpleNamespace(_sock=sock, close=lambda: None)
    api = SimpleNamespace(
        exec_create=lambda _id, argv, **kw: commands.append(argv) or {"Id": "operation"},
        exec_start=lambda *a, **kw: connection,
        exec_inspect=lambda _: {"Running": False, "ExitCode": 0},
    )
    backend = SimpleNamespace(
        _project_postgres=lambda: SimpleNamespace(id="only-own-db"),
        project_postgres_password="test",
        client=SimpleNamespace(api=api),
    )
    monkeypatch.setattr(module, "read_controller_output", lambda *a, **kw: b"{}")
    module.admin_sql(backend, "SET statement_timeout=0;", lifetime_seconds=2)
    assert commands[0][:5] == ["timeout", "-s", "KILL", "2", "psql"]


def test_witnessed_r0_record_does_not_execute_target_sql_again():
    sql = migration_sql(
        {"drizzle/0002.sql": "CREATE TABLE notes(id int);"},
        verify_only=False,
        record_witnessed=True,
    )
    assert "INSERT INTO public.__omnia_project_migrations" in sql
    assert "EXECUTE" not in sql


def test_r1_extra_project_journal_does_not_change_business_schema_compatibility():
    from yleum_orchestrator.services.restoration_data_contract import DataContract, assess_contract

    business = {"name": "notes", "columns": [{"name": "id", "type": "integer"}]}
    journal = {"name": "__omnia_project_migrations", "columns": [
        {"name": "name", "type": "text"}, {"name": "sha256", "type": "text"},
        {"name": "applied_at", "type": "timestamptz"}]}
    old = DataContract.model_validate({"version": 1, "tables": [business]})
    current = DataContract.model_validate({"version": 1, "tables": [business, journal]})
    assert assess_contract(old, current).blockers == []
