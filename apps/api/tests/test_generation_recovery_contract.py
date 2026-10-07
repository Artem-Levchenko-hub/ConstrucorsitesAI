"""Recovery compiles only complete trees and preserves fresh reads and empty files."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services.generation import agent_recovery


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
