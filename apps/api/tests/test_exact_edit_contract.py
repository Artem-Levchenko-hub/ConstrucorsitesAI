"""Договор точечной правки: настоящий исполнитель ячейки, транспорт-заглушка.

Все случаи идут через существующий одноразовый стенд PostgreSQL — другой среды
у проекта больше нет. Алгоритм точечной правки здесь не повторяется.
"""

from dataclasses import dataclass

from yleum_api.services import agent_builder as ab

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
    assert harness.hot_reload_calls == []  # Ячейка кладёт ровно то, что написал агент.


async def test_disposable_db_cell_rejected_fence_does_not_change_local_file(
    monkeypatch,
    db_session,
    test_engine,
):
    from tests.test_project_cell_executor import _prepare_executor
    from yleum_api.services import project_cell_executor
    from yleum_api.services.orchestrator_client import OrchestratorBadRequest

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
