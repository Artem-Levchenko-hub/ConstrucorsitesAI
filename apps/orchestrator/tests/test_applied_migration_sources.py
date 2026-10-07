import hashlib
import json
from types import SimpleNamespace

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceError

PATH = "drizzle/0002.sql"
SQL = "CREATE TABLE coffee(id int);\r\n-- кофе\n"
SHA = hashlib.sha256(SQL.encode("utf-8")).hexdigest()


@pytest.fixture
def source_guard(tmp_path, monkeypatch):
    from yleum_orchestrator.services import applied_migration_sources as module

    catalog = {"database_identity": "a" * 64, "applied": {}, "journal": True,
               "has_relations": False}
    backend = SimpleNamespace(metadata_path=tmp_path / "docker.json",
                              project_postgres_volume="db-volume")
    monkeypatch.setattr(module, "admin_sql", lambda *a, **kw: json.dumps(catalog).encode())
    return module, backend, catalog


def test_intent_survives_crash_and_journal_promotes_only_committed_sql(source_guard):
    module, backend, catalog = source_guard
    module.save_migration_intent(backend, {PATH: SQL})
    assert module.protected_sources(backend, {PATH: "pending edit"}) == {}
    catalog["applied"] = {PATH: SHA}  # DB commit happened; controller process died.
    assert module.protected_sources(backend, {}) == {PATH: SQL}


@pytest.mark.parametrize("desired", [{}, {PATH: "changed"}])
def test_changed_or_deleted_applied_sql_is_rejected(source_guard, desired):
    module, backend, catalog = source_guard
    catalog["applied"] = {PATH: SHA}
    protected = module.protected_sources(backend, {PATH: SQL})
    with pytest.raises(CellResourceError, match="applied migration"):
        module.require_protected_sources(protected, desired)


def test_pending_sql_stays_editable_and_new_migration_is_allowed(source_guard):
    module, backend, catalog = source_guard
    catalog["applied"] = {PATH: SHA}
    protected = module.protected_sources(backend, {PATH: SQL})
    module.require_protected_sources(protected, {PATH: SQL, "drizzle/0003.sql": "new"})


@pytest.mark.parametrize("change", ["identity", "volume"])
def test_stale_database_archive_cannot_recover_missing_source(source_guard, change):
    module, backend, catalog = source_guard
    module.save_migration_intent(backend, {PATH: SQL})
    catalog["applied"] = {PATH: SHA}
    if change == "identity":
        catalog["database_identity"] = "b" * 64
    else:
        backend.project_postgres_volume = "restored-volume"
    with pytest.raises(CellResourceError, match="trusted source"):
        module.protected_sources(backend, {})


def test_old_database_without_journal_is_not_guessed(source_guard):
    module, backend, catalog = source_guard
    catalog.update(journal=False, has_relations=True)
    with pytest.raises(CellResourceError, match="reconciliation"):
        module.protected_sources(backend, {PATH: SQL})


def test_journal_mismatch_without_witness_never_adopts_draft(source_guard):
    module, backend, catalog = source_guard
    catalog["applied"] = {PATH: SHA}
    with pytest.raises(CellResourceError, match="trusted source"):
        module.protected_sources(backend, {PATH: "unproven"})
    assert not (backend.metadata_path.parent / "migration-sources.json").exists()


@pytest.mark.parametrize("outcome", [
    "ok", "nonzero", "timeout", "exception", "deleted", "unsafe_writer", "commit_then_fail",
])
async def test_shell_physical_corruption_is_restored_even_on_failure(source_guard, outcome):
    from uuid import uuid4

    from yleum_orchestrator.services.docker_cell_resources import DockerCommandResult
    from yleum_orchestrator.services.machine_adapter import MachineAdapter

    _module, backend, catalog = source_guard
    catalog["applied"] = {PATH: SHA}
    files = {PATH: SQL, "drizzle/0003.sql": "pending"}
    stopped = []
    container = SimpleNamespace(status="running", reload=lambda: None)

    def stop():
        stopped.append(True)
        if outcome != "unsafe_writer":
            container.status = "exited"

    async def read(_):
        return {path: source.encode() for path, source in files.items()}

    async def write(_, values):
        assert container.status == "exited"
        files.update({path: source.decode() for path, source in values.items()})

    backend.workspace_volume = "source-volume"
    backend.stop_machine = stop
    backend._container = lambda: container
    runtime = MachineAdapter(SimpleNamespace(docker=SimpleNamespace(
        read_workspace_source_files=read, write_volume_files=write,
    )), SimpleNamespace())
    operation_id = uuid4()
    saved = {"operations": {str(operation_id): {}}}
    machine = SimpleNamespace(state=lambda: saved,
                              path=backend.metadata_path.parent / "machine.json")
    runtime.parts = lambda _: (machine, backend)
    runtime.exists = lambda _: True
    state = SimpleNamespace(workspace_id=uuid4(), active_generation_run_id=None, operations=())

    async def execute(*_):
        if outcome == "commit_then_fail":
            _module.save_migration_intent(backend, files)
            catalog["applied"] = {PATH: SHA}
            raise RuntimeError("build failed after SQL commit")
        files["drizzle/0003.sql"] = "pending edit"
        if outcome == "deleted":
            del files[PATH]
        else:
            files[PATH] = "changed by shell"
        if outcome == "exception":
            raise RuntimeError("command transport failed")
        if outcome == "timeout":
            raise TimeoutError("command timed out")
        return DockerCommandResult(exit_code=1 if outcome == "nonzero" else 0,
                                   output="", timed_out=False)

    runtime.execute = execute
    if outcome == "commit_then_fail":
        catalog["applied"] = {}
        with pytest.raises(RuntimeError, match="after SQL commit"):
            await runtime.guarded_execute(state, None,
                                          SimpleNamespace(operation_id=operation_id))
        assert _module.protected_sources(backend, {}) == {PATH: SQL}
        assert stopped == []
        return
    with pytest.raises(CellResourceError, match="applied migration"):
        await runtime.guarded_execute(state, None,
                                      SimpleNamespace(operation_id=operation_id))
    assert files == {PATH: "changed by shell" if outcome == "unsafe_writer" else SQL,
                     "drizzle/0003.sql": "pending edit"}
    assert stopped == [True]
    assert saved["operations"][str(operation_id)]["result"]["exit_code"] == 1
    with pytest.raises(CellResourceError, match="use a new operation"):
        await runtime.guarded_execute(state, None,
                                      SimpleNamespace(operation_id=operation_id))


@pytest.mark.parametrize("role_failure", [False, True])
async def test_apply_archives_exact_source_before_commit_even_if_postcommit_roles_fail(
    source_guard, monkeypatch, role_failure,
):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.services import machine_adapter, project_migrations

    source_module, backend, catalog = source_guard
    operation_id = uuid4()
    saved = {"manifest": payload(), "operations": {str(operation_id): {}}}
    machine = SimpleNamespace(state=lambda: saved, assert_ready=AsyncMock(),
                              path=backend.metadata_path.parent / "machine.json")
    runtime = machine_adapter.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda _: (machine, backend)
    runtime._migration_inventory = AsyncMock(return_value={PATH: SQL})
    backend.prepare_project_database_migrations = lambda _: None
    role_calls = []

    def roles():
        role_calls.append(True)
        if len(role_calls) == 2 and role_failure:
            raise CellResourceError("post-commit role bootstrap failed")

    def commit(*_, **kwargs):
        archived = json.loads((backend.metadata_path.parent / "migration-sources.json").read_text())
        assert archived["sources"][PATH].encode() == SQL.encode()
        catalog["applied"] = {PATH: SHA}
        return json.dumps({"contract": "project-migrations-v1", "source_digest": "c" * 64,
                           "database_identity": "a" * 64, "catalog_digest": "b" * 64}).encode()

    backend.bootstrap_project_database_roles = roles
    monkeypatch.setattr(project_migrations, "admin_sql", lambda *a, **kw: b"a" * 64)
    monkeypatch.setattr(project_migrations, "migrator_sql", commit)
    request = SimpleNamespace(operation_id=operation_id, fencing_epoch=7, cmd="omnia:full_build",
                              task_role="full_build", expected_revision="d" * 64,
                              timeout_seconds=60, generation_run_id=uuid4())
    state = SimpleNamespace(
        workspace_id=uuid4(), active_generation_run_id=request.generation_run_id, operations=(),
    )
    if role_failure:
        with pytest.raises(CellResourceError, match="verification failed"):
            await runtime._project_migrations(state, request, verify_applied=False)
    else:
        await runtime._project_migrations(state, request, verify_applied=False)
        receipt = json.loads(machine.path.read_text())["operations"][str(operation_id)]
        assert receipt["project_migration_receipt"]["database_identity"] == "a" * 64
        assert "compiled_asset_receipt" not in receipt
    assert source_module.protected_sources(backend, {}) == {PATH: SQL}


async def test_first_write_after_release_resumes_database_before_readonly_guard(
    source_guard, monkeypatch,
):
    from uuid import uuid4

    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.services.machine_adapter import MachineAdapter

    module, backend, catalog = source_guard
    catalog["applied"] = {PATH: SHA}
    events = []
    postgres = None

    async def ensure(manifest, mutation):
        nonlocal postgres
        assert mutation.fencing_epoch == 7
        assert manifest.services[0].argv == ["python", "server.py"]
        events.append("resume")
        postgres = SimpleNamespace(status="running", reload=lambda: None)

    backend._project_postgres = lambda: postgres
    def query(*_, **kwargs):
        assert postgres is not None and postgres.status == "running"
        return json.dumps(catalog).encode()

    monkeypatch.setattr(module, "admin_sql", query)
    machine = SimpleNamespace(state=lambda: {"manifest": payload()}, ensure=ensure)
    runtime = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda _: (machine, backend)
    runtime.exists = lambda _: True
    state = SimpleNamespace(workspace_id=uuid4(), active_generation_run_id=uuid4(),
                            fencing_epoch=7, operations=())
    assert await runtime.protect_migration_sources(state, {PATH: SQL}) == {PATH: SQL}
    assert events == ["resume"]
    await runtime.protect_migration_sources(state, {PATH: SQL})
    assert events == ["resume"]


@pytest.mark.parametrize("journal", [False, True])
async def test_readonly_adaptation_allows_app_edits_without_adopting_historical_sql(
    source_guard, monkeypatch, journal,
):
    from uuid import uuid4

    from yleum_orchestrator.services import machine_adapter
    from yleum_orchestrator.services.docker_cell_resources import DockerCommandResult

    source_module, backend, catalog = source_guard
    catalog.update(journal=journal, has_relations=True, applied={PATH: SHA} if journal else {})
    run = uuid4()
    state = SimpleNamespace(workspace_id=uuid4(), active_generation_run_id=run,
                            operations=(), fencing_epoch=7)
    machine = SimpleNamespace(state=lambda: {"restoration_adaptation_run_id": str(run)})
    files = {PATH: SQL, "src/app.ts": "original"}
    backend.workspace_volume = "source"
    backend._project_postgres = lambda: SimpleNamespace(status="running", reload=lambda: None)
    backend.bootstrap_project_database_roles = lambda: pytest.fail("guard must not bootstrap roles")
    backend.prepare_project_database_migrations = lambda _: pytest.fail("adaptation must not apply")
    queries = []

    def query(_, sql, **kwargs):
        queries.append(sql)
        assert "BEGIN READ ONLY" in sql
        return json.dumps(catalog).encode()

    monkeypatch.setattr(source_module, "admin_sql", query)

    async def read(_):
        return {p: s.encode() for p, s in files.items()}

    runtime = machine_adapter.MachineAdapter(SimpleNamespace(docker=SimpleNamespace(
        read_workspace_source_files=read,
    )), SimpleNamespace())
    runtime.parts = lambda _: (machine, backend)
    runtime.exists = lambda _: True
    assert await runtime.protect_migration_sources(state, {**files, "src/app.ts": "edited"}) == (
        {PATH: SQL} if journal else {}
    )

    async def execute(*_):
        files["src/app.ts"] = "shell edit"
        return DockerCommandResult(exit_code=0, output="done", timed_out=False)

    runtime.execute = execute
    result = await runtime.guarded_execute(state, None, SimpleNamespace(operation_id=uuid4()))
    assert result.exit_code == 0 and files["src/app.ts"] == "shell edit"
    archived = json.loads((backend.metadata_path.parent / "migration-sources.json").read_text())
    assert archived["sources"] == ({PATH: SQL} if journal else {})
    assert len(queries) == 3
    if journal:
        with pytest.raises(CellResourceError, match="applied migration"):
            await runtime.protect_migration_sources(state, {**files, PATH: "changed"})
    else:
        # A retained marker cannot relax checks for the next ordinary generation.
        state.active_generation_run_id = uuid4()
        with pytest.raises(CellResourceError, match="reconciliation"):
            await runtime.protect_migration_sources(state, files)
