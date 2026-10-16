"""Recovery records fail closed without mutating the caller's JSON."""
from __future__ import annotations

import importlib
import importlib.util
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.services.generation import agent_recovery


def contract():
    name = "yleum_api.services.generation.recovery_contract"
    assert importlib.util.find_spec(name) is not None, "missing versioned recovery validation"
    return importlib.import_module(name)


def bootstrap():
    rc = contract()
    return rc.RecoveryState(identity=rc.RecoveryIdentity(
        run_id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64,
        accepted_snapshot_id=None, supersedes_run_id=None,
    ))


def test_absent_contract_and_strict_json_roundtrip():
    rc = contract()
    state = bootstrap()
    assert rc.decode_recovery(None) is None
    assert rc.decode_recovery(state.model_dump(mode="json")) == state
    assert state.cursor == "bootstrap"
    assert state.binding is None and state.checkpoint is None and state.context_ref is None


@pytest.mark.parametrize("raw", [
    {"version": 99}, {}, {"version": True}, {"version": "1"}, [], "bad", 0,
])
def test_unknown_contract_is_preserved_and_blocks_activation(raw):
    rc = contract()
    before = json.dumps(raw)
    with pytest.raises(rc.RecoveryContractError):
        rc.decode_recovery(raw)
    assert json.dumps(raw) == before


@pytest.mark.parametrize("patch", [
    {"attempt_seq": True}, {"state_revision": "1"}, {"consecutive_no_progress": -1},
    {"active_work_ms": -1}, {"stage_active_work_ms": {"edit": -1}},
    {"stage_active_work_ms": {"other": 1}}, {"surprise": "unsafe"},
    {"cursor": "agent"}, {"cursor": "finalization"},
    {"incomplete_response_budget": {"total": 1, "since_progress": 2}},
])
def test_corrupt_fields_are_rejected(patch):
    rc = contract()
    raw = bootstrap().model_dump(mode="json") | patch
    with pytest.raises(rc.RecoveryContractError):
        rc.decode_recovery(raw)


def test_records_and_nested_work_counters_are_immutable():
    state = bootstrap()
    with pytest.raises(ValidationError):
        state.attempt_seq = 3
    with pytest.raises(TypeError):
        state.stage_active_work_ms["edit"] = 3


def test_requirement_scope_and_claim_run_must_match():
    rc = contract()
    raw = bootstrap().model_dump(mode="json")
    raw["requirements"] = [{
        "id": "orders", "text": "Save orders", "origin_prompt_sha256": "b" * 64,
        "check_kind": "db_persistence", "required": True,
    }]
    with pytest.raises(rc.RecoveryContractError):
        rc.decode_recovery(raw)
    raw["requirements"] = []
    raw["continuation_claim"] = {
        "run_id": str(uuid4()), "token": str(uuid4()), "attempt_seq": 0,
        "worker_id": "worker", "expires_at": "2026-10-16T12:00:00Z",
    }
    with pytest.raises(rc.RecoveryContractError):
        rc.decode_recovery(raw)


def test_configuration_defaults_off_and_rejects_unknown_mode():
    from yleum_api.core.config import Settings

    settings = Settings(_env_file=None)
    assert getattr(settings, "generation_recovery_mode", None) == "off"
    assert settings.generation_recovery_canary_project_ids == ""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_recovery_mode="automatic")


@pytest.mark.parametrize("kind", ["snapshot", "max_core"])
async def test_recovery_never_builds_a_half_restored_dependency_tree(kind, monkeypatch):
    schema = "src/lib/db/schema.ts"
    product = "src/app/page.tsx"
    baseline = {schema: "export const maxUsers = {};", "empty.ts": ""}
    tree = {
        **baseline,
        schema: baseline[schema] + "export const workouts = {};",
        product: "import { workouts } from '@/lib/db/schema';",
    }
    observed = []

    async def stage(writes, deletes):
        tree.update(writes)
        for path in deletes:
            tree.pop(path, None)

    async def sync():
        observed.append(dict(tree))
        if product in tree and "workouts" not in tree[schema]:
            return SimpleNamespace(failure="missing workouts export")
        return SimpleNamespace(failure=None)

    async def build():
        assert tree == baseline
        return {"ok": True}

    async def render():
        return dict(baseline)

    monkeypatch.setattr(agent_recovery.repo_svc, "read_files", lambda *_: dict(baseline))
    handle = SimpleNamespace(stage_patch=stage, sync_preview=sync)
    kwargs = dict(
        project_id=uuid4(),
        project_slug="recovery",
        handle=handle,
        touched_files={schema: tree[schema], product: tree[product]},
        probe_build=build,
    )
    if kind == "snapshot":
        result = await agent_recovery._restore_touched_tree(**kwargs, baseline_sha="snapshot-sha")
    else:
        result = await agent_recovery._restore_max_core(**kwargs, render_core=render)
    assert result == {"ok": True}
    assert observed == [baseline]


@pytest.mark.parametrize("kind", ["snapshot", "max_core"])
@pytest.mark.parametrize("fault", [None, "patch", "build"])
async def test_recovery_restores_before_building(kind, fault, monkeypatch):
    trace = []
    project_id, handle = uuid4(), SimpleNamespace()
    version = {"kept.ts": "first", "empty.ts": ""} if kind == "snapshot" else {}
    touched = {"kept.ts": "candidate", "empty.ts": "candidate", "new.ts": "candidate"}

    def read(pid, sha):
        assert (pid, sha) == (project_id, "snapshot-sha")
        trace.append("read")
        return dict(version)

    async def render():
        trace.append("render")
        return dict(version)

    async def apply(**kwargs):
        assert kwargs["project_cell_handle"] is handle
        trace.append(("patch", dict(kwargs["files"])))
        assert kwargs["empty_files"] == (("empty.ts",) if kind == "snapshot" else ())
        if fault == "patch":
            raise RuntimeError("patch")

    async def build():
        trace.append("build")
        if fault == "build":
            raise RuntimeError("build")
        return {"ok": True, "detail": "verified"}

    monkeypatch.setattr(agent_recovery.repo_svc, "read_files", read)
    monkeypatch.setattr(agent_recovery, "_apply_project_cell_preview_files", apply)

    async def restore():
        common = dict(
            project_id=project_id,
            project_slug="recovery",
            handle=handle,
            touched_files=touched,
            probe_build=build,
        )
        if kind == "snapshot":
            return await agent_recovery._restore_touched_tree(**common, baseline_sha="snapshot-sha")
        return await agent_recovery._restore_max_core(**common, render_core=render)

    expected = (
        [
            "read",
            ("patch", {"kept.ts": "first", "empty.ts": "", "new.ts": ""}),
            "build",
        ]
        if kind == "snapshot"
        else [
            "render",
            ("patch", {"empty.ts": "", "kept.ts": "", "new.ts": "", "src/app/page.tsx": ""}),
            "build",
        ]
    )
    if fault:
        with pytest.raises(RuntimeError, match=fault):
            await restore()
        fault_index = next(
            i
            for i, step in enumerate(expected)
            if step == fault or (isinstance(step, tuple) and step[0] == fault)
        )
        assert trace == expected[: fault_index + 1]
    else:
        assert await restore() == {"ok": True, "detail": "verified"}
        assert trace == expected
        version["kept.ts"] = "fresh config or snapshot"
        trace.clear()
        assert await restore() == {"ok": True, "detail": "verified"}
        assert trace[0] == ("read" if kind == "snapshot" else "render")
        assert trace[1][1]["kept.ts"] == "fresh config or snapshot"
