"""Crash outcomes must reflect the running code, without another activation."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.schemas.code_restoration import CodeRestorationApply
from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
from omnia_orchestrator.services.project_machine import write_controller_json


def test_current_source_inventory_rejects_untracked_business_files():
    import hashlib

    from omnia_orchestrator.services.code_restoration_engine import verify_source_inventory

    expected = [{"path": "src/app/page.tsx", "sha256": hashlib.sha256(b"source").hexdigest()}]
    verify_source_inventory(
        {"src/app/page.tsx": b"source", "next-env.d.ts": b"generated"}, expected
    )
    with pytest.raises(ValueError, match="файлы"):
        verify_source_inventory(
            {"src/app/page.tsx": b"source", "uploads/client.pdf": b"data"}, expected
        )
    with pytest.raises(ValueError):
        verify_source_inventory({"src/app/page.tsx": b"changed"}, expected)


def test_source_inventory_checks_binary_and_empty_files():
    import hashlib

    from omnia_orchestrator.services.code_restoration_engine import verify_source_inventory

    actual = {"public/photo.png": b"\x00\xff", "src/empty": b""}
    expected = [
        {"path": name, "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in actual.items()
    ]
    verify_source_inventory(actual, expected)
    with pytest.raises(ValueError):
        verify_source_inventory({**actual, "public/photo.png": b"another"}, expected)


class Lock:
    @asynccontextmanager
    async def hold(self, _workspace):
        yield


def request():
    return CodeRestorationApply(
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        owner_id=UUID(int=4),
        candidate_id=UUID(int=5),
        expected_source_head="a" * 40,
        target_commit_sha="b" * 40,
        planned_commit_sha="c" * 40,
        expected_fencing_epoch=3,
        fencing_epoch=4,
        report_revision=1,
    )


class Engine(CodeRestorationEngine):
    def __init__(self, tmp_path, *, matches=True):
        self.root = tmp_path
        self.matches = matches
        self.calls = []
        self.state = SimpleNamespace(
            workspace_id=UUID(int=2),
            project_id=UUID(int=3),
            owner_id=UUID(int=4),
            fencing_epoch=4,
            active_generation_run_id=None,
        )
        self.backend = SimpleNamespace()
        self.manager = SimpleNamespace(
            operation_lock=Lock(),
            state_store=SimpleNamespace(load=lambda _: self.state),
            machine_runtime=SimpleNamespace(parts=lambda _: (None, self.backend)),
        )

    def _manager(self, _workspace):
        return self.manager

    async def _running_matches(self, *args):
        self.calls.append("observe")
        return self.matches

    async def _recover_old(self, *args):
        self.calls.append("recover")
        self.matches = True

    async def _complete_activation(self, *args):
        self.calls.append("complete")
        return "d" * 64

    async def _activate_code(self, *args):
        pytest.fail("Observation must not blindly replay activation")


def save_intent(engine, stage):
    value = request()
    intent = {
        **value.model_dump(
            mode="json",
            include={
                "operation_id",
                "workspace_id",
                "project_id",
                "owner_id",
                "candidate_id",
                "fencing_epoch",
            },
        ),
        "source_commit_sha": value.planned_commit_sha,
        "source_revision": "d" * 64,
        "volume": "new-code",
        "state": stage,
        "old": {"workspace_volume": "old-code", "machine": {"manifest": {}}},
    }
    write_controller_json(engine._directory(value.operation_id) / "activation.json", intent)
    return value


async def test_lost_success_response_observes_without_restarting_app(tmp_path):
    engine = Engine(tmp_path)
    value = save_intent(engine, "active")
    result = await engine.observe(value, {"manifest": {}})
    assert result["applied"] is True
    assert engine.calls == ["observe", "complete"]


async def test_archive_cleanup_failure_does_not_undo_observed_activation(tmp_path, monkeypatch):
    from pathlib import Path

    engine = Engine(tmp_path)
    value = save_intent(engine, "active")

    def denied(*args, **kwargs):
        raise PermissionError("archive busy")

    monkeypatch.setattr(Path, "unlink", denied)
    result = await engine.observe(value, {"manifest": {}})
    assert result["applied"] is True
    assert "recover" not in engine.calls


@pytest.mark.parametrize("stage", ["intent", "switching", "reverting"])
async def test_crash_before_proven_switch_restores_old_code_and_releases_exact_fence(
    tmp_path, stage
):
    engine = Engine(tmp_path, matches=False)
    value = save_intent(engine, stage)
    result = await engine.observe(value, {"manifest": {}})
    assert result["applied"] is False and result["safe_to_release"] is True
    assert result["fencing_epoch"] == value.fencing_epoch
    assert engine.calls.count("recover") == 1
    assert (
        json.loads((engine._directory(value.operation_id) / "activation.json").read_text())["state"]
        == "reverted"
    )


async def test_repeated_negative_observation_does_not_restart_recovered_app(tmp_path):
    engine = Engine(tmp_path)
    value = save_intent(engine, "reverted")
    assert (await engine.observe(value, {}))["applied"] is False
    assert engine.calls == ["observe"]


async def test_old_operation_cannot_revert_newer_generation(tmp_path):
    engine = Engine(tmp_path)
    value = save_intent(engine, "active")
    engine.state.fencing_epoch = 5
    with pytest.raises(RuntimeError):
        await engine.observe(value, {})
    assert engine.calls == []


def untouched_engine(tmp_path, monkeypatch, *, target_exists=False, physical_epoch=3):
    from omnia_orchestrator.routers.runtime import _workspace_revision

    engine = Engine(tmp_path)
    engine.state.fencing_epoch = 3
    engine.manager.profile = SimpleNamespace(state_path=tmp_path / "state.json")
    engine.manager.machine_runtime.exists = lambda _: True
    engine.backend = SimpleNamespace(
        stem="fixture",
        workspace_volume="old-code",
        client=SimpleNamespace(volumes=object()),
        _metadata=lambda: {"epoch": physical_epoch},
        _lookup=lambda *_: object() if target_exists else None,
        _container=lambda: SimpleNamespace(
            id="app", labels={"omnia.fencing_epoch": str(physical_epoch)}
        ),
        _project_postgres=lambda: SimpleNamespace(
            id="pg", labels={"omnia.fencing_epoch": str(physical_epoch)}
        ),
    )
    machine = SimpleNamespace(state=lambda: {"epoch": physical_epoch, "manifest": {}})
    engine.manager.machine_runtime.parts = lambda _: (machine, engine.backend)
    monkeypatch.setattr(
        "omnia_orchestrator.services.code_restoration_engine.load_policy", lambda _: None
    )

    async def read(*_):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr("omnia_orchestrator.routers.workspace._read_agent_workspace_files", read)
    return engine, {
        "workspace_revision": _workspace_revision({"src/page.tsx": "current"}),
        "source_runtime": engine._runtime_identity(engine.backend, machine),
        "live_contract": {},
        "manifest": {},
    }


@pytest.mark.parametrize("physical_epoch", [2, 3])
async def test_crash_before_activation_journal_recovers_exact_admitted_fence(
    tmp_path, monkeypatch, physical_epoch
):
    engine, prepared = untouched_engine(tmp_path, monkeypatch, physical_epoch=physical_epoch)
    result = await engine.observe(request(), prepared)
    assert result == {
        "candidate_id": str(UUID(int=5)),
        "source_commit_sha": "c" * 40,
        "fencing_epoch": 4,
        "applied": False,
        "safe_to_release": True,
    }
    assert engine.calls == ["recover"]
    assert (
        json.loads((engine._directory(UUID(int=1)) / "activation.json").read_text())["state"]
        == "reverted"
    )


async def test_missing_journal_with_possible_external_effect_cannot_claim_safe_release(
    tmp_path, monkeypatch
):
    engine, prepared = untouched_engine(tmp_path, monkeypatch, target_exists=True)
    with pytest.raises(RuntimeError, match="ambiguous"):
        await engine.observe(request(), prepared)
    assert engine.calls == []


async def test_coordinator_restart_closes_gap_before_engine_journal(tmp_path, monkeypatch):
    import base64

    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare
    from omnia_orchestrator.services.code_restorations import CodeRestorationService

    engine, prepared = untouched_engine(tmp_path / "engine", monkeypatch)

    async def prepare(_):
        return {
            **prepared,
            "state": "ready",
            "candidate_id": str(UUID(int=5)),
            "report": {
                "revision": 1,
                "mode": "adapted",
                "changes": [],
                "retained_data": [],
                "unavailable_features": [],
                "warnings": [],
                "blockers": [],
                "next_actions": [],
            },
        }

    async def failed_apply(*_):
        raise RuntimeError("controller failed before creating activation intent")

    engine.prepare, engine.apply = prepare, failed_apply
    service = CodeRestorationService(root=tmp_path / "journal", engine=engine)
    body = request()
    await service.prepare(
        CodeRestorationPrepare(
            **body.model_dump(
                exclude={
                    "candidate_id",
                    "report_revision",
                    "expected_fencing_epoch",
                    "fencing_epoch",
                }
            ),
            fencing_epoch=3,
            files=[{"path": "src/page.tsx", "content_base64": base64.b64encode(b"old").decode()}],
        )
    )
    await service.drain()
    await service.apply(body)
    await service.drain()
    assert service._read(body.workspace_id, body.operation_id)["state"] == "reconciling"
    restarted = CodeRestorationService(root=tmp_path / "journal", engine=engine)
    await restarted.recover()
    await restarted.drain()
    observed = restarted._read(body.workspace_id, body.operation_id)
    assert observed["state"] == "failed"
    assert observed["observed"]["safe_to_release"] is True
    assert engine.calls == ["recover"]
