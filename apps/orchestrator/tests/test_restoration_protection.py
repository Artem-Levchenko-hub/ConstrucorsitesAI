"""Adaptive tools cannot observe an owner DB or outrun protection recovery."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services import restoration_protection as protection
from omnia_orchestrator.services.project_machine import write_controller_json
from omnia_orchestrator.services.restoration_data_contract import DataContract
from omnia_orchestrator.services.restoration_database import load_policy


def fixture(tmp_path, monkeypatch, *, blockers=()):
    monkeypatch.setattr(protection, "DockerMachineBackend", SimpleNamespace)
    calls = []
    manifest = MachineManifest.model_validate(
        {
            "version": 1,
            "tasks": [{"name": "build", "role": "full_build", "argv": ["pnpm", "build"]}],
            "services": [
                {"name": "web", "argv": ["pnpm", "start"], "readiness": {"port": 3000, "path": "/"}}
            ],
            "routes": [{"path": "/", "service": "web", "port": 3000}],
        }
    )
    files = {
        ".omnia/cell.json": manifest.model_dump_json(),
        "pnpm-lock.yaml": "lock",
        "package.json": json.dumps(
            {"dependencies": {"next": "16.2.1"}, "scripts": {"start": "next start"}}
        ),
    }
    contract = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "contacts",
                    "owner_column": "owner_id",
                    "columns": [
                        {"name": "owner_id", "type": "text"},
                        {"name": "name", "type": "text"},
                        {"name": "surname", "type": "text"},
                    ],
                }
            ],
        }
    )
    state = SimpleNamespace(
        workspace_id=UUID(int=1),
        project_id=UUID(int=2),
        owner_id=UUID(int=3),
        active_generation_run_id=UUID(int=4),
        active_generation_fencing_epoch=5,
        fencing_epoch=5,
    )
    request = SimpleNamespace(generation_run_id=UUID(int=4), fencing_epoch=5)
    metadata = {"manifest": manifest.model_dump(mode="json"), "restored_image": "old-rootfs"}
    product = SimpleNamespace(status="running")
    attached = [product]

    def stop(**kwargs):
        product.status = "exited"
        calls.append("stop_writers")

    def remove_product(**kwargs):
        attached.clear()
        calls.append("remove_product")

    product.stop, product.remove, product.reload = stop, remove_product, lambda: None
    backend = SimpleNamespace(
        root=tmp_path,
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        owner_id=state.owner_id,
        workspace_volume="same-code",
        metadata_path=tmp_path / "runtime.json",
        _metadata=lambda: metadata.copy(),
        _container=lambda: attached[0] if attached else None,
        environment_volume_names=lambda _: ("same-code", "same-database", "same-business"),
    )

    def ensure(*args):
        calls.append("ensure_protected" if load_policy(backend) else "ensure_owner")
        product.status = "running"
        attached[:] = [product]

    backend.ensure = ensure
    backend.remove = lambda: (attached.clear(), calls.append("remove_old_pg"))
    backend.start_service = lambda *args: calls.append("start_service")
    backend.service_status = lambda *args, **kwargs: {"ready": True}
    machine_state = {"manifest": manifest.model_dump(mode="json"), "epoch": 5}
    machine = SimpleNamespace(path=tmp_path / "machine.json", state=lambda: machine_state.copy())
    manager = SimpleNamespace(state_store=SimpleNamespace(load=lambda _: state))

    async def checkpoint(_state, *, volumes, persist):
        assert volumes == () and persist is False
        assert product.status != "running"
        calls.append("capture_rootfs_only")
        return SimpleNamespace(
            image_id="saved-rootfs",
            model_dump=lambda **kwargs: {"image_id": "saved-rootfs", "volumes": []},
        )

    adapter = SimpleNamespace(
        manager=manager,
        parts=lambda _: (machine, backend),
        exists=lambda _: True,
        checkpoint=checkpoint,
        _start_boundary=lambda *args: calls.append("signed_boundary"),
    )

    async def effect(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(protection, "machine_effect", effect)
    monkeypatch.setattr(protection, "validate_retained_runtime", lambda *_: None)
    monkeypatch.setattr(protection, "require_supported_driver", lambda *_: None)
    monkeypatch.setattr(protection, "catalog_contract", lambda *_: (contract, list(blockers)))

    def install(_backend):
        assert load_policy(backend) is not None
        calls.append("install_policy")

    monkeypatch.setattr(protection, "install_policy", install)
    from omnia_orchestrator.routers import workspace

    async def read_files(*_):
        return files

    monkeypatch.setattr(workspace, "_read_agent_workspace_files", read_files)
    return adapter, state, request, files, backend, calls


async def test_first_adaptation_installs_protection_after_stopping_writers(tmp_path, monkeypatch):
    adapter, state, request, files, backend, calls = fixture(tmp_path, monkeypatch)
    await protection.protect_current_database(adapter, state, request, files)
    assert (
        calls.index("stop_writers") < calls.index("remove_old_pg") < calls.index("install_policy")
    )
    assert calls.index("capture_rootfs_only") < calls.index("remove_old_pg")
    assert (
        calls.index("install_policy")
        < calls.index("start_service")
        < calls.index("signed_boundary")
    )
    assert load_policy(backend)["epoch"] == 5
    assert protection.protection_journal(backend)["volumes"] == [
        "same-business",
        "same-code",
        "same-database",
    ]
    assert protection.pending_protection(backend) is None
    protection.require_protection_ready(adapter, state)


@pytest.mark.parametrize("phase", ["prepared", "rootfs_saved", "policy_staged"])
async def test_each_durable_interruption_blocks_tools_then_reconciles_original_lease(
    tmp_path,
    monkeypatch,
    phase,
):
    adapter, state, request, files, backend, calls = fixture(tmp_path, monkeypatch)
    interrupted = False

    def crashing_save(path, value):
        nonlocal interrupted
        write_controller_json(path, value)
        if (
            path == protection.protection_path(backend)
            and value["state"] == phase
            and not interrupted
        ):
            interrupted = True
            raise RuntimeError("injected process loss after durable stage")

    monkeypatch.setattr(protection, "write_controller_json", crashing_save)
    with pytest.raises(RuntimeError, match="injected"):
        await protection.protect_current_database(adapter, state, request, files)
    with pytest.raises(CellResourceError, match="pending"):
        protection.require_protection_ready(adapter, state)
    await protection.reconcile_generation_protection(adapter, state)
    assert protection.pending_protection(backend) is None
    assert load_policy(backend)["epoch"] == request.fencing_epoch
    assert "start_service" not in calls  # Release/cancel recovery does not restart product writers.


async def test_unknown_schema_fails_before_policy_or_model_tools(tmp_path, monkeypatch):
    adapter, state, request, files, backend, calls = fixture(
        tmp_path, monkeypatch, blockers=["unknown ownership"]
    )
    with pytest.raises(CellResourceError, match="explicit protection support"):
        await protection.protect_current_database(adapter, state, request, files)
    assert load_policy(backend) is None and protection.protection_journal(backend) is None
    assert "start_service" not in calls and "install_policy" not in calls


async def test_unsupported_driver_fails_before_journal_or_policy(tmp_path, monkeypatch):
    adapter, state, request, files, backend, calls = fixture(tmp_path, monkeypatch)

    def reject(_):
        raise CellResourceError("requires installed pg >=8.22,<9")

    monkeypatch.setattr(protection, "require_supported_driver", reject)
    with pytest.raises(CellResourceError, match="requires installed pg"):
        await protection.protect_current_database(adapter, state, request, files)
    assert load_policy(backend) is None and protection.protection_journal(backend) is None
    assert "stop_writers" not in calls and "install_policy" not in calls


@pytest.mark.parametrize(
    "version,expected", [("8.21.0", 1), ("8.22.0", 0), ("8.23.1", 0), ("9.0.0", 1), ("unknown", 1)]
)
def test_driver_probe_reads_metadata_without_loading_project_code(version, expected):
    from omnia_orchestrator.services.docker_machine_backend import _archive_file

    data = _archive_file("package.json", json.dumps({"name": "pg", "version": version}).encode())

    def read(path):
        assert path == "/workspace/node_modules/pg/package.json"
        return [data], {}

    backend = SimpleNamespace(_container=lambda: SimpleNamespace(get_archive=read))
    if expected:
        with pytest.raises(CellResourceError, match="requires installed pg"):
            protection.require_supported_driver(backend)
    else:
        protection.require_supported_driver(backend)


@pytest.mark.parametrize("kind", ["oversized", "symlink", "invalid_json"])
def test_driver_probe_rejects_untrusted_metadata_archives(kind):
    import io
    import tarfile

    from omnia_orchestrator.services.docker_machine_backend import _archive_file

    data = _archive_file("package.json", b"not json")
    if kind == "oversized":
        data = b"x" * 131073
    elif kind == "symlink":
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            entry = tarfile.TarInfo("package.json")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/private/key"
            archive.addfile(entry)
        data = output.getvalue()
    backend = SimpleNamespace(
        _container=lambda: SimpleNamespace(get_archive=lambda _: ([data], {}))
    )
    with pytest.raises(CellResourceError, match="requires installed pg"):
        protection.require_supported_driver(backend)


async def test_lost_readiness_response_stops_partial_services_before_policy_replay(
    tmp_path, monkeypatch
):
    adapter, state, request, files, backend, calls = fixture(tmp_path, monkeypatch)
    adapter._start_boundary = lambda *_: (_ for _ in ()).throw(RuntimeError("lost boundary"))
    with pytest.raises(RuntimeError, match="lost boundary"):
        await protection.protect_current_database(adapter, state, request, files)
    assert "start_service" in calls
    assert protection.pending_protection(backend)["state"] == "policy_staged"
    policy = load_policy(backend)
    calls.clear()
    await protection.reconcile_generation_protection(adapter, state)
    assert calls.index("remove_old_pg") < calls.index("install_policy")
    assert "start_service" not in calls
    assert load_policy(backend) == policy
    assert protection.pending_protection(backend) is None


async def test_changed_source_cannot_resume_admitted_protection(tmp_path, monkeypatch):
    adapter, state, request, files, backend, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        protection, "install_policy", lambda _: (_ for _ in ()).throw(RuntimeError("stop"))
    )
    with pytest.raises(RuntimeError):
        await protection.protect_current_database(adapter, state, request, files)
    with pytest.raises(CellIdentityConflict, match="envelope changed"):
        await protection.protect_current_database(
            adapter, state, request, {**files, "new.ts": "changed"}
        )
    assert protection.pending_protection(backend) is not None


async def test_new_generation_cannot_claim_old_protection_journal(tmp_path, monkeypatch):
    adapter, state, request, files, backend, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        protection, "install_policy", lambda _: (_ for _ in ()).throw(RuntimeError("stop"))
    )
    with pytest.raises(RuntimeError):
        await protection.protect_current_database(adapter, state, request, files)
    state.active_generation_run_id = UUID(int=44)
    state.active_generation_fencing_epoch = state.fencing_epoch = 6
    with pytest.raises(CellIdentityConflict, match="lease changed"):
        await protection.reconcile_generation_protection(adapter, state)
    assert protection.pending_protection(backend) is not None
