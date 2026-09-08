"""Exact-edit contract: real executors, mocked transport, no models or live runtimes.

Cell cases reuse the existing disposable PostgreSQL harness;
only container cases run locally. No replica of the exact-edit algorithm.
"""

from dataclasses import dataclass

import pytest

from omnia_api.services import agent_builder as ab
from omnia_api.services import orchestrator_client

NOT_FOUND = "search text not found exactly; read the file and copy it byte-for-byte"
NOT_UNIQUE = "search text is not unique; add surrounding lines"
INVALID = "edit_file needs path, search, replace"


@dataclass(frozen=True)
class Case:
    name: str
    current: str | None
    search: object
    replacement: object
    expected: str | None = None
    error: str | None = None


CASES = [
    Case("unique", "before needle after", "needle", "done", "before done after"),
    Case("absent", "abc", "d", "x", error=NOT_FOUND),
    Case("duplicate", "abc abc", "abc", "x", error=NOT_UNIQUE),
    Case("empty-empty", "", "", "x", "x"),
    Case("empty-nonempty", "abc", "", "x", error=NOT_UNIQUE),
    Case("case-sensitive", "Needle", "needle", "x", error=NOT_FOUND),
    Case("unicode-not-normalized", "cafe\u0301", "caf\u00e9", "x", error=NOT_FOUND),
    Case("crlf-not-normalized", "a\r\nb", "a\nb", "x", error=NOT_FOUND),
    Case("exact-crlf", "a\r\nb", "a\r\n", "x\r\n", "x\r\nb"),
    Case("overlap-count", "aaa", "aa", "X", "Xa"),
    Case("nonoverlap-duplicate", "aaaa", "aa", "X", error=NOT_UNIQUE),
    Case("zero", "old", "old", 0, "0"),
    Case("false", "old", "old", False, "False"),
    Case("list", "old", "old", ["a", 0], "['a', 0]"),
    Case("dict", "old", "old", {"x": 1}, "{'x': 1}"),
    Case("none", "old", "old", None, error=INVALID),
    Case("bad-search", "old", 0, "new", error=INVALID),
    Case("missing-file", None, "old", "new", error="not found: src/a.txt"),
    Case("delete-content", "old", "old", "", ""),
]


async def container(monkeypatch, current, *, reload_result=None):
    reads, writes = [], []

    async def read(project, slug, path):
        reads.append((project, slug, path))
        return current

    async def reload(**kwargs):
        writes.append(kwargs)
        return reload_result or {"state": "hot_reloaded"}

    monkeypatch.setattr(orchestrator_client, "agent_read_file", read)
    monkeypatch.setattr(orchestrator_client, "hot_reload", reload)
    execute = ab.make_container_executor(project_id="fixture-project", slug="fixture-slug")
    return execute, reads, writes


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_container_exact_edit_original_consumer(case, monkeypatch):
    execute, reads, writes = await container(monkeypatch, case.current)
    observation = await execute(
        ab.Action(
            name="edit_file",
            args={"path": "src/a.txt", "search": case.search, "replace": case.replacement},
        )
    )
    if case.error:
        assert observation == {"ok": False, "error": case.error}
        assert writes == []
        assert len(reads) == (0 if case.error == INVALID else 1)
    else:
        assert observation == {
            "ok": True,
            "content": case.expected,
            "files": {},
            "detail": "patched src/a.txt",
        }
        assert writes == [
            {
                "project_id": "fixture-project",
                "slug": "fixture-slug",
                "files": {"src/a.txt": case.expected},
            }
        ]


async def test_container_runtime_failure_keeps_written_files_and_generated_lock(monkeypatch):
    execute, _reads, writes = await container(
        monkeypatch,
        "old",
        reload_result={
            "state": "hot_reloaded",
            "package_exit_code": 1,
            "package_stderr_tail": "bad dependency",
            "pnpm_lockfile": "lockfileVersion: '9.0'\n",
        },
    )
    observation = await execute(
        ab.Action(
            name="edit_file", args={"path": "package.json", "search": "old", "replace": "new"}
        )
    )
    assert observation == {
        "ok": False,
        "error": "runtime apply failed after patching package.json: "
        "package_exit_code=1: bad dependency",
        "content": "new",
        "files": {"package.json": "new", "pnpm-lock.yaml": "lockfileVersion: '9.0'\n"},
    }
    assert len(writes) == 1  # Runtime failure follows an attempted write; no false no-write claim.


@pytest.mark.parametrize(
    "path,new,expected",
    [
        ("src/app/nested/layout.tsx", "<html><body>Hello</body></html>", "<div>Hello</div>"),
        ("src/a.css", "a {}\n@import 'x';\n", "@import 'x';\na {}\n"),
        ("./src/a.txt", "new", "new"),
    ],
)
async def test_container_keeps_own_sanitizers_and_raw_path(path, new, expected, monkeypatch):
    execute, reads, writes = await container(monkeypatch, "old")
    observation = await execute(
        ab.Action(name="edit_file", args={"path": path, "search": "old", "replace": new})
    )
    assert observation["content"] == expected
    assert reads[0][2] == path and writes[0]["files"] == {path: expected}


async def test_disposable_db_cell_exact_edit_original_consumer(
    monkeypatch,
    db_session,
    test_engine,
):
    # Reuse real control/lease setup with isolated transport; never fake the cell closure.
    from tests.test_project_cell_executor import _prepare_executor

    for case in CASES:
        initial = {} if case.current is None else {"src/a.txt": case.current}
        harness = await _prepare_executor(
            monkeypatch, db_session, test_engine, snapshot_files=initial
        )
        observation = await harness.handle.execute(
            ab.Action(
                name="edit_file",
                args={"path": "src/a.txt", "search": case.search, "replace": case.replacement},
            )
        )
        if case.error:
            assert observation == {"ok": False, "error": case.error}, case.name
            assert harness.write_calls == [], case.name
        else:
            assert observation == {
                "ok": True,
                "content": case.expected,
                "detail": "patched src/a.txt",
            }, case.name
            assert harness.write_calls == [
                {
                    "generation_run_id": harness.run_id,
                    "fencing_epoch": 1,
                    "expected_revision": f"{1:064x}",
                    "files": {"src/a.txt": case.expected},
                    "deletes": [],
                }
            ], case.name
        assert harness.hot_reload_calls == [] and harness.legacy_actions == []


async def test_disposable_db_cell_preserves_its_path_and_content_contract(
    monkeypatch,
    db_session,
    test_engine,
):
    from tests.test_project_cell_executor import _prepare_executor

    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files={"src/app/nested/layout.tsx": "old"}
    )
    html = "<html><body>Hello</body></html>"
    observation = await harness.handle.execute(
        ab.Action(
            name="edit_file",
            args={"path": "./src/app/nested/layout.tsx", "search": "old", "replace": html},
        )
    )
    assert observation == {
        "ok": True,
        "content": html,
        "detail": "patched src/app/nested/layout.tsx",
    }
    assert harness.write_calls[0]["files"] == {"src/app/nested/layout.tsx": html}
    assert harness.hot_reload_calls == []  # Cell stages exact content; no legacy sanitizers.


async def test_disposable_db_cell_rejected_fence_does_not_change_local_file(
    monkeypatch,
    db_session,
    test_engine,
):
    from omnia_api.services import project_cell_executor
    from omnia_api.services.orchestrator_client import OrchestratorBadRequest
    from tests.test_project_cell_executor import _prepare_executor

    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files={"src/a.txt": "old"}
    )
    attempts = []

    async def reject(workspace_id, **kwargs):
        attempts.append((workspace_id, kwargs))
        raise OrchestratorBadRequest("stale fence", 409)

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_write_files", reject)
    observation = await harness.handle.execute(
        ab.Action(name="edit_file", args={"path": "src/a.txt", "search": "old", "replace": "new"})
    )
    assert observation == {"ok": False, "error": "stale fence"}
    assert attempts == [
        (
            harness.workspace_id,
            {
                "generation_run_id": harness.run_id,
                "fencing_epoch": 1,
                "expected_revision": f"{1:064x}",
                "files": {"src/a.txt": "new"},
                "deletes": (),
            },
        )
    ]
    read = await harness.handle.execute(ab.Action(name="read_file", args={"path": "src/a.txt"}))
    assert read == {"ok": True, "content": "old"}
    assert harness.write_calls == [] and harness.hot_reload_calls == []
