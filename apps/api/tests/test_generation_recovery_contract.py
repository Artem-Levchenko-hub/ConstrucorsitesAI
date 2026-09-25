"""Recovery mechanics preserve fresh reads and ordered write/delete/build effects."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services.generation import agent_recovery


@pytest.mark.parametrize("kind", ["snapshot", "max_core"])
@pytest.mark.parametrize("fault", [None, "write", "delete", "build"])
async def test_recovery_restores_before_deleting_and_building(kind, fault, monkeypatch):
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
        assert kwargs["project_id"] == project_id
        assert kwargs["project_slug"] == "recovery" and kwargs["project_cell_handle"] is handle
        stage = "write" if len([x for x in trace if isinstance(x, tuple)]) == 0 else "delete"
        trace.append((stage, dict(kwargs["files"])))
        if stage == fault:
            raise RuntimeError(stage)

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
            ("write", {"kept.ts": "first", "empty.ts": ""}),
            ("delete", {"new.ts": ""}),
            "build",
        ]
        if kind == "snapshot"
        else [
            "render",
            ("write", {}),
            ("delete", {"empty.ts": "", "kept.ts": "", "new.ts": "", "src/app/page.tsx": ""}),
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
