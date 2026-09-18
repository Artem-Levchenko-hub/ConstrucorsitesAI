"""AV19.1 (controller side): a cancel recorded before or during preparation is
honoured at the next safe checkpoint — never after a full install/build, and
never as a false "failed"."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
from omnia_orchestrator.services.code_restorations import CodeRestorationService
from tests.test_code_restoration_engine import Lock, plain_prepare_request
from tests.test_code_restorations import Engine, cancel_request, request, service, status

_READY_REPORT = {
    "revision": 1, "mode": "exact", "changes": [], "retained_data": [],
    "unavailable_features": [], "warnings": [], "blockers": [], "next_actions": [],
}


async def test_cancel_recorded_before_preparation_never_builds_a_candidate(tmp_path):
    engine = Engine()
    svc = service(tmp_path, engine)
    svc._schedule = lambda workspace, operation: None  # drive by hand below
    value = request()
    await svc.prepare(value)
    await svc.cancel(cancel_request(value))

    await svc._drive(value.workspace_id, value.operation_id)

    assert engine.calls == ["cancel"]  # no prepare, no candidate
    observed = await status(svc, value)
    assert observed["state"] == "cancelled" and observed["can_apply"] is False


async def test_cancel_during_preparation_stops_at_the_next_checkpoint(tmp_path):
    class CheckpointEngine(Engine):
        def __init__(self):
            super().__init__()
            self.seen: list[bool] = []
            self.svc: CodeRestorationService | None = None

        async def prepare(self, value, *, cancel_requested):
            self.calls.append("prepare")
            self.seen.append(cancel_requested())  # checkpoint 1: nobody cancelled yet
            assert self.svc is not None
            await self.svc.cancel(cancel_request(value))  # owner clicks cancel mid-install
            self.seen.append(cancel_requested())  # checkpoint 2: stop here, no build
            return {
                "state": "cancelled",
                "candidate_id": None,
                "report": {**_READY_REPORT, "mode": "adapted", "blockers": ["отменено"]},
            }

    engine = CheckpointEngine()
    svc = service(tmp_path, engine)
    engine.svc = svc
    value = request()
    await svc.prepare(value)
    await svc.drain()

    assert engine.seen == [False, True]
    assert engine.calls == ["prepare", "cancel"]
    observed = await status(svc, value)
    assert observed["state"] == "cancelled" and observed["phase"] == "cancelled"
    assert observed["report"]["blockers"] == ["отменено"]


def test_engines_without_checkpoints_keep_the_bare_signature(tmp_path):
    svc = service(tmp_path, Engine())
    assert svc._prepare_options(Engine(), UUID(int=2), UUID(int=1)) == {}

    class Aware:
        async def prepare(self, value, *, cancel_requested):
            return {}

    options = svc._prepare_options(Aware(), UUID(int=2), UUID(int=1))
    assert set(options) == {"cancel_requested"} and options["cancel_requested"]() is False


async def test_engine_prepare_returns_cancelled_after_install_without_build(tmp_path, monkeypatch):
    """Real engine, faked machine: cancel lands after `pnpm install`; the candidate
    is cleaned up and neither the build nor the start ever run."""
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services import project_machine
    from omnia_orchestrator.services.restoration_data_contract import DataContract
    from omnia_orchestrator.services.versioning.contracts import InventoryReport
    from tests.test_project_machine_manifest import payload

    manifest = MachineManifest.model_validate(payload())
    contract = DataContract.model_validate({"version": 1, "tables": [{
        "name": "price_list",
        "columns": [{"name": "id", "type": "uuid"}, {"name": "title", "type": "text"}],
    }]})
    events: list[str] = []
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(cell_required_free_disk_bytes=0)
    source = SimpleNamespace(
        workspace_volume="live-code", is_running=lambda: True, name="source",
        _metadata=lambda: {"epoch": 3}, _container=lambda: None, _project_postgres=lambda: None,
    )
    machine = SimpleNamespace(state=lambda: {"epoch": 3, "manifest": manifest.model_dump()})
    candidate = SimpleNamespace(
        name="candidate", workspace_volume="candidate-code", base_image="image",
        stop=lambda: events.append("stop"),
    )

    async def read_sources(_volume):
        return {}

    manager = SimpleNamespace(
        operation_lock=Lock(),
        machine_runtime=SimpleNamespace(parts=lambda _: (machine, source)),
        docker=SimpleNamespace(read_workspace_source_files=read_sources),
    )
    engine._manager = lambda _: manager
    engine._state = lambda *args, **kwargs: SimpleNamespace(workspace_id=UUID(int=2))
    monkeypatch.setattr(module, "validate_supported_runtime", lambda _files: manifest)
    monkeypatch.setattr(module, "verify_source_inventory", lambda *_: None)
    monkeypatch.setattr(project_machine, "machine_remaining_seconds", lambda value: value or 1)

    async def workspace_files(*_):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", workspace_files
    )
    monkeypatch.setattr(module, "catalog_contract", lambda backend: (contract, []))
    monkeypatch.setattr(module, "describe_live_catalog", lambda backend: (contract, [], []))
    monkeypatch.setattr(module, "candidate_contract", lambda *_: contract)
    monkeypatch.setattr(module, "admin_sql", lambda *a, **k: b"")
    monkeypatch.setattr(
        module, "observe_database",
        lambda backend, *, observed_on: InventoryReport(
            presence="present", coverage="complete", schema_analysis="complete",
            observed_on=observed_on,
        ),
    )
    engine._dump = lambda backend: b"COPY price_list;"

    async def make_candidate(*_):
        events.append("candidate")
        return candidate

    async def start(backend, _manifest, epoch):
        events.append("start")

    async def cleanup(*_):
        events.append("cleanup")

    engine._candidate = make_candidate
    engine._seed_source = lambda *_: events.append("seed")
    engine._command = lambda backend, argv, *_: events.append("command:" + " ".join(argv[:2]))
    engine._disable_egress = lambda backend: events.append("egress-off")
    engine._start = start
    engine._verify_source = lambda *_: events.append("verify")
    engine._capture_code = lambda *_args, **_kwargs: "digest"
    engine._cleanup_candidate = cleanup

    result = await engine.prepare(
        plain_prepare_request(),
        cancel_requested=lambda: "command:pnpm install" in events,
    )

    assert result["state"] == "cancelled" and result["candidate_id"] is None
    assert result["report"]["blockers"] == ["Подготовка восстановления отменена владельцем."]
    assert events.count("command:pnpm install") == 1
    assert "egress-off" not in events and "start" not in events
    assert events[-1] == "cleanup"
    assert not (tmp_path / str(plain_prepare_request().operation_id) / "prepared.json").exists()


@pytest.mark.parametrize("flag", [True, False])
async def test_cancel_before_any_stage_returns_without_touching_the_machine(
    tmp_path, monkeypatch, flag
):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services import code_restoration_engine as module
    from tests.test_project_machine_manifest import payload

    class MachineTouched(Exception):
        pass

    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(cell_required_free_disk_bytes=0)

    def manager(_workspace):
        raise MachineTouched

    engine._manager = manager
    manifest = MachineManifest.model_validate(payload())
    monkeypatch.setattr(module, "validate_supported_runtime", lambda _files: manifest)
    if flag:
        result = await engine.prepare(plain_prepare_request(), cancel_requested=lambda: True)
        assert result["state"] == "cancelled"
    else:
        # Without a cancel the engine proceeds to the machine.
        with pytest.raises(MachineTouched):
            await engine.prepare(plain_prepare_request(), cancel_requested=lambda: False)


async def test_an_intent_that_lands_during_a_drive_is_looked_at_again(tmp_path):
    """A cancel recorded after the running drive's last check is not lost until
    the next restart: the finished drive re-schedules itself once."""
    svc = service(tmp_path, Engine())
    workspace, operation = UUID(int=2), UUID(int=1)
    drives: list[int] = []
    gate = asyncio.Event()

    async def drive(_workspace, _operation):
        drives.append(len(drives))
        if len(drives) == 1:
            await gate.wait()

    svc._drive = drive  # type: ignore[method-assign]
    svc._schedule(workspace, operation)
    svc._schedule(workspace, operation)  # lands while the first drive runs
    assert len(svc._tasks) == 1 and svc._rerun == {str(operation)}
    gate.set()
    await svc.drain()

    assert drives == [0, 1] and not svc._tasks and not svc._rerun
