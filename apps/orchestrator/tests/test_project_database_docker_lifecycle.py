from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_docker_machine_backend import backend
from yleum_orchestrator.services import restoration_database
from yleum_orchestrator.services.project_database_credentials import ProjectDatabaseCredentialStore
from yleum_orchestrator.services.project_machine import write_controller_json


def test_generated_environment_uses_independent_runtime_authority(tmp_path):
    runtime = backend(tmp_path)
    env = runtime.project_database_env()
    credentials = ProjectDatabaseCredentialStore(tmp_path / "project-db-credentials").load(
        runtime.workspace_id
    )
    assert env["PGUSER"] == "omnia_project_runtime"
    assert env["PGPASSWORD"] == credentials.runtime_password
    assert credentials.admin_password not in str(env)
    assert credentials.migrator_password not in str(env)
    assert runtime.project_postgres_password not in str(env)


def test_protocol_without_authority_fails_closed(tmp_path):
    runtime = backend(tmp_path)
    write_controller_json(runtime.metadata_path, {"project_database_role_protocol": 1})
    with pytest.raises(RuntimeError, match="authority unavailable"):
        runtime.project_database_env()


def test_controller_admin_uses_peer_socket_without_secret_environment():
    args, env = restoration_database.admin_args(SimpleNamespace())
    assert args == ["-h", "/tmp", "-U", "postgres", "-d", "postgres"]
    assert env == {"PGOPTIONS": restoration_database.TRUSTED_ADMIN_PGOPTIONS}
    assert "-c search_path=public" in env["PGOPTIONS"]
    assert "-c log_statement=none" in env["PGOPTIONS"]
    assert "-c session_preload_libraries=" in env["PGOPTIONS"]


def test_postgres_exposes_private_peer_socket(tmp_path):
    runtime = backend(tmp_path)
    options = runtime._project_postgres_options("guard", 1)
    assert "unix_socket_directories=/tmp" in options["command"]
    assert options["labels"]["omnia.project_database_role_protocol"] == "1"
    assert "hba_file=/var/lib/postgresql/data/pg_hba.conf" in options["command"]


def test_migration_uses_limited_login_and_reconciles_afterwards(monkeypatch):
    import json

    from yleum_orchestrator.services import project_migrations

    events = []
    identity = "c" * 64

    def admin(_backend, sql, **kwargs):
        events.append(("admin", sql))
        return identity.encode()

    def migrator(_backend, sql, **kwargs):
        events.append(("migrator", sql))
        assert "pg_control_system()" not in sql
        assert identity in sql
        return json.dumps(
            {
                "contract": "project-migrations-v1",
                "source_digest": "a" * 64,
                "database_identity": identity,
                "catalog_digest": "b" * 64,
            }
        ).encode()

    runtime = SimpleNamespace(
        bootstrap_project_database_roles=lambda: events.append(("bootstrap", ""))
    )
    monkeypatch.setattr(project_migrations, "admin_sql", admin)
    monkeypatch.setattr(project_migrations, "migrator_sql", migrator, raising=False)
    project_migrations.run_project_migrations(
        runtime, {"drizzle/0002.sql": "CREATE TABLE notes(id int)"}, verify_only=False
    )
    assert [event[0] for event in events] == [
        "bootstrap",
        "admin",
        "admin",
        "migrator",
        "bootstrap",
    ]


def test_bootstrap_failure_stops_owned_product_and_preserves_protocol(tmp_path, monkeypatch):
    from yleum_orchestrator.core.cell_resources import CellResourceError

    runtime = backend(tmp_path)
    stopped = []
    runtime.stop_machine = lambda: stopped.append(True)
    runtime._install_project_role_hba = lambda: None
    monkeypatch.setattr(
        restoration_database,
        "admin_sql",
        lambda *a, **kw: (_ for _ in ()).throw(CellResourceError("private diagnostics")),
    )
    with pytest.raises(CellResourceError, match="activation fenced"):
        runtime.bootstrap_project_database_roles()
    assert stopped == [True]
    assert "project_database_role_protocol" not in runtime._metadata()


def test_migration_quiesce_stops_children_before_restarting_idle_machine(tmp_path):
    from tests.test_docker_machine_backend import stale_service_fixture

    runtime, _manifest, _service, events, children, _ = stale_service_fixture(
        tmp_path, missing=False
    )
    runtime.prepare_project_database_migrations(7)
    assert children == [0]
    assert events.index("stop") < events.index("start")
    assert runtime._metadata()["services"] == {}
    assert "postgres-reused" not in events


def test_migration_quiesce_cannot_stop_newer_machine(tmp_path):
    from tests.test_docker_machine_backend import stale_service_fixture
    from yleum_orchestrator.core.cell_resources import CellIdentityConflict

    runtime, _, _, events, children, _ = stale_service_fixture(tmp_path, missing=False)
    with pytest.raises(CellIdentityConflict):
        runtime.prepare_project_database_migrations(6)
    assert children == [1]
    assert "stop" not in events


def test_migration_quiesce_kills_unregistered_daemon_before_idle_restart(tmp_path):
    from tests.test_docker_machine_backend import stale_service_fixture

    runtime, _, _, events, children, _ = stale_service_fixture(tmp_path, missing=False)
    machine = runtime._container()
    metadata = runtime._metadata()
    metadata["services"] = {}
    write_controller_json(runtime.metadata_path, metadata)

    def stop(*, timeout):
        events.append("stop-daemon")
        children[0] = 0
        machine.status = "exited"

    machine.stop = stop
    runtime.prepare_project_database_migrations(7)
    assert children == [0]
    assert events.index("stop-daemon") < events.index("start")
    assert runtime._metadata()["exec_pids"] == {}


def test_adapter_advertises_runtime_and_controller_owned_schema():
    from yleum_orchestrator.services.machine_adapter import MachineAdapter

    capabilities = MachineAdapter(SimpleNamespace(), SimpleNamespace()).capabilities()
    assert capabilities["database_admin"] == "runtime-crud"
    assert capabilities["database_migrations"] == "controller-limited-login"


def test_adapter_credentials_are_stable_and_missing_protocol_authority_is_rejected(tmp_path):
    from yleum_orchestrator.services.machine_adapter import MachineAdapter

    adapter = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state")), SimpleNamespace()
    )
    workspace_id = uuid4()
    first = adapter.project_database_credentials(workspace_id)
    assert adapter.project_database_credentials(workspace_id) == first
    write_controller_json(
        adapter.root / str(workspace_id) / "docker.json", {"project_database_role_protocol": 1}
    )
    (adapter.root / "project-db-credentials" / f"{workspace_id}.json").unlink()
    with pytest.raises(RuntimeError, match="authority unavailable"):
        adapter.project_database_credentials(workspace_id)


async def test_adapter_quiesces_owned_preview_before_migration_and_defers_service_resume(
    tmp_path, monkeypatch
):
    import json
    from unittest.mock import AsyncMock

    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.core.project_machine import MachineManifest
    from yleum_orchestrator.services import applied_migration_sources, machine_adapter

    events = []
    manifest = MachineManifest.model_validate(payload())
    operation_id = uuid4()
    saved = {"manifest": manifest.model_dump(mode="json"), "operations": {str(operation_id): {}}}
    machine = SimpleNamespace(
        state=lambda: saved,
        path=tmp_path / "machine.json",
        assert_ready=AsyncMock(side_effect=lambda *_: events.append("fence")),
    )
    runtime = SimpleNamespace(
        prepare_project_database_migrations=lambda epoch: events.append("quiesce"),
        metadata_path=tmp_path / "docker.json",
        project_postgres_volume="project-database",
    )
    adapter = machine_adapter.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    adapter.parts = lambda state: (machine, runtime)
    adapter._migration_inventory = AsyncMock(return_value={"drizzle/0002.sql": "SELECT 1"})
    adapter._request_digest = lambda *_: "a" * 64

    def migrate(*args, **kwargs):
        witness = json.loads((tmp_path / "migration-sources.json").read_text())
        assert witness["sources"] == {"drizzle/0002.sql": "SELECT 1"}
        events.append("migrate")
        return {"contract": "project-migrations-v1"}

    def catalog(*args, **kwargs):
        events.append("catalog")
        return json.dumps({"database_identity": "c" * 64, "journal": False,
                           "has_relations": False, "applied": {}}).encode()

    monkeypatch.setattr(applied_migration_sources, "admin_sql", catalog)
    monkeypatch.setattr(machine_adapter, "run_project_migrations", migrate)
    request = SimpleNamespace(
        operation_id=operation_id, fencing_epoch=7, generation_run_id=uuid4(), expected_revision=1
    )
    state = SimpleNamespace(
        workspace_id=uuid4(), active_generation_run_id=request.generation_run_id, operations=()
    )
    await adapter._project_migrations(state, request, verify_applied=False)
    assert events == ["fence", "quiesce", "catalog", "migrate"]
    operation = json.loads(machine.path.read_text())["operations"][str(operation_id)]
    assert operation["project_migration_receipt"]["contract"] == "project-migrations-v1"
    assert "compiled_asset_receipt" not in operation


def test_legacy_admin_environment_is_recreated_at_same_epoch(tmp_path):
    from tests.test_docker_machine_backend import stale_service_fixture

    runtime, manifest, _, _, _, _ = stale_service_fixture(tmp_path, missing=False)
    machine = runtime._container()
    machine.attrs["Config"]["Env"] = [
        "PGUSER=postgres",
        "PGPASSWORD=" + runtime.project_postgres_password,
    ]
    events = []
    runtime._checkpoint_for_recreate = lambda selected: events.append(
        ("checkpoint", selected.digest())
    )
    runtime.remove = lambda **kwargs: events.append(("remove", kwargs["expected_epoch"]))

    class ReachedNetwork(Exception):
        pass

    def network(_name):
        raise ReachedNetwork()

    runtime.client.networks.get = network
    with pytest.raises(ReachedNetwork):
        runtime.ensure(manifest, 7)
    assert events == [("checkpoint", manifest.digest()), ("remove", 7)]
