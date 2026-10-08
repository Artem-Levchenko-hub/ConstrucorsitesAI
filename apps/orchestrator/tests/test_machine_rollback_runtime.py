"""Rollback must replace the candidate process without applying SQL again."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
from yleum_orchestrator.services import machine_adapter, next_compilation_capture


@pytest.mark.parametrize(
    "failure", [None, "migration", "build", "receipt", "source", "start", "timeout"]
)
async def test_rollback_rebuilds_and_replaces_only_product_verifying_existing_ledger(
    monkeypatch,
    failure,
):
    trace = []
    running = {"candidate": True, "restored": False}
    saved = {"epoch": 7, "operations": {}}
    receipt = {"collector_version": "next-fixed-data-v1"}

    def remove():
        trace.append("remove_product")
        running.update(candidate=False, restored=False)

    async def ensure(*args):
        trace.append("ensure")

    async def start(mutation, **kwargs):
        saved["operations"][str(mutation.operation_id)] = {}

    async def exec_start(argv, cwd, mutation):
        trace.append(argv)
        return argv

    async def exec_status(argv, mutation):
        return NS(
            state="completed",
            exit_code=1 if failure == "build" and argv == ["pnpm", "build"] else 0,
            output="test result",
        )

    machine = NS(
        ensure=ensure,
        request_start=start,
        request_heartbeat=AsyncMock(),
        request_finish=AsyncMock(side_effect=lambda _, result: result),
        exec_start=exec_start,
        exec_status=exec_status,
        state=lambda: saved,
        inspect_request_status=AsyncMock(
            return_value=NS(
                result=None,
                state="running",
                phase="activate",
            )
        ),
        path="owned-controller-state",
    )
    backend = NS(remove_machine=remove, stop_machine=remove)
    runtime = machine_adapter.MachineAdapter(NS(), NS())
    runtime.parts = lambda _: (machine, backend)

    async def migrations(state, request, *, verify_applied):
        assert verify_applied is True, "rollback must never replay/apply candidate SQL"
        trace.append("verify_ledger")
        if failure == "migration":
            raise machine_adapter.CellResourceError("missing applied migration")
        return {"contract": "project-migrations-v1"}

    async def activate(*args):
        trace.append("activate")
        if failure == "timeout":
            raise TimeoutError()
        if failure == "start":
            raise machine_adapter.MachineServiceFailed("restored readiness failed")
        assert not running["candidate"]
        running["restored"] = True

    runtime._project_migrations = migrations
    runtime._activate_runtime = activate
    runtime._require_restored_source_revision = AsyncMock(
        side_effect=machine_adapter.CellResourceError("restored source changed during build")
        if failure == "source"
        else None
    )
    runtime._store_migration_receipt = lambda *_: None
    monkeypatch.setattr(machine_adapter, "write_controller_json", lambda *_: None)
    monkeypatch.setattr(
        next_compilation_capture,
        "capture_next_compilation",
        lambda *_: None if failure == "receipt" else receipt,
    )
    value = payload()
    value["tasks"] = [
        {"name": "build", "role": "full_build", "argv": ["pnpm", "build"]},
        {"name": "test", "role": "full_build", "argv": ["pnpm", "test"]},
    ]
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:restore_runtime",
        task_role="restore_runtime",
    )
    result = await runtime.execute(NS(), MachineManifest.model_validate(value), request)
    assert result.exit_code == (0 if failure is None else 124 if failure == "timeout" else 1)
    assert not running["candidate"]
    assert running["restored"] is (failure is None)
    assert trace.index("remove_product") < trace.index("verify_ledger")
    if failure == "source":
        assert "activate" not in trace
    if failure is None:
        assert (
            trace.index("remove_product") < trace.index(["pnpm", "build"]) < trace.index("activate")
        )
        assert saved["operations"][str(request.operation_id)]["compiled_asset_receipt"] == receipt


async def test_rollback_replay_does_not_kill_restored_runtime():
    runtime = machine_adapter.MachineAdapter(NS(), NS())
    backend = NS(remove_machine=pytest.fail, stop_machine=pytest.fail)
    machine = NS(
        ensure=AsyncMock(),
        request_start=AsyncMock(
            return_value=NS(
                exit_code=0,
                output="already restored",
                timed_out=False,
            )
        ),
    )
    runtime.parts = lambda _: (machine, backend)
    value = payload()
    value["tasks"] = [
        {"name": "build", "role": "full_build", "argv": ["pnpm", "build"]},
        {"name": "test", "role": "full_build", "argv": ["pnpm", "test"]},
    ]
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:restore_runtime",
        task_role="restore_runtime",
    )
    result = await runtime.execute(NS(), MachineManifest.model_validate(value), request)
    assert result.exit_code == 0


async def test_failed_rollback_replay_cannot_stop_successful_successor_same_epoch():
    runtime = machine_adapter.MachineAdapter(NS(), NS())
    backend = NS(remove_machine=pytest.fail, stop_machine=pytest.fail)
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:restore_runtime",
        task_role="restore_runtime",
    )
    machine = NS(
        ensure=AsyncMock(),
        request_start=AsyncMock(
            return_value=NS(
                exit_code=1,
                output="previous failed attempt",
                timed_out=False,
            )
        ),
        state=lambda: {"epoch": 7, "operations": {str(request.operation_id): {}}},
    )
    runtime.parts = lambda _: (machine, backend)
    value = payload()
    value["tasks"] = [
        {"name": "build", "role": "full_build", "argv": ["pnpm", "build"]},
        {"name": "test", "role": "full_build", "argv": ["pnpm", "test"]},
    ]
    result = await runtime.execute(NS(), MachineManifest.model_validate(value), request)
    assert result.exit_code == 1
