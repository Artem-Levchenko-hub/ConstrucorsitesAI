from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-at-least-32-bytes")

from omnia_api.core.errors import ApiError
from omnia_api.routers import messages
from omnia_api.services.agent_builder import Action


@pytest.mark.asyncio
async def test_prepare_max_runtime_context_selects_project_cell_once_without_legacy_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    emitted: list[tuple[str, dict[str, object]]] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        return {"ok": True, "detail": action.name}

    async def fake_maybe_create(**kwargs):
        calls.append((str(kwargs["project_id"]), str(kwargs["project_slug"])))
        return SimpleNamespace(execute=fake_execute)

    async def fake_emit(event: str, data: dict[str, object]) -> None:
        emitted.append((event, dict(data)))

    async def fake_ensure() -> None:
        pytest.fail("legacy ensure_provisioned path must stay unused for selected cell")

    monkeypatch.setattr(
        messages.project_cell_executor,
        "maybe_create_project_cell_executor",
        fake_maybe_create,
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_sandbox_capabilities",
        lambda *args, **kwargs: pytest.fail("legacy sandbox attestation must stay unused"),
    )

    result = await messages._prepare_max_runtime_context(
        project_id=uuid4(),
        project_slug="max-cell",
        user_id=uuid4(),
        generation_run_id=uuid4(),
        vision_context="ctx",
        legacy_execute=lambda _action: pytest.fail("legacy executor must stay unused"),
        max_shell_requested=True,
        ensure_legacy_runtime_ready=fake_ensure,
        agent_emit=fake_emit,
        max_model_locked_files=frozenset({"src/app/page.tsx"}),
        max_security_locked_files=frozenset({"src/app/api/max/route.ts"}),
    )

    assert len(calls) == 1
    assert result["project_cell_handle"] is not None
    assert result["base_agent_executor"] is result["project_cell_handle"].execute
    assert result["max_shell_enabled"] is True
    assert result["active_max_locked_files"] == frozenset({"src/app/api/max/route.ts"})
    assert result["agent_result"] is None
    assert emitted == [
        (
            "agent.step",
            {
                "step": 0,
                "action": "project_cell",
                "human": "Подключаю owner-only Project Cell",
                "path": "",
                "detail": (
                    "Кодовая генерация идёт в изолированном workspace; "
                    "preview/runtime синхронизируются только для проверки."
                ),
                "ok": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_execute_max_agent_action_routes_selected_see_without_legacy_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        seen.append(action.name)
        return {"ok": True, "detail": "cell see"}

    monkeypatch.setattr(
        messages.orchestrator_client,
        "create_max_preview_session",
        lambda *args, **kwargs: pytest.fail("legacy MAX preview bootstrap must stay unused"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "get_status",
        lambda *args, **kwargs: pytest.fail("legacy status lookup must stay unused"),
    )

    result = await messages._execute_max_agent_action(
        Action(name="see", args={"path": "/"}),
        project_id=uuid4(),
        project_slug="max-cell",
        vision_context="ctx",
        base_agent_executor=lambda _action: pytest.fail("legacy executor must stay unused"),
        max_shell_enabled=False,
        project_cell_handle=SimpleNamespace(execute=fake_execute),
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {"ok": True, "detail": "cell see"}
    assert seen == ["see"]


@pytest.mark.asyncio
async def test_build_agent_seed_parts_reads_from_selected_cell_without_legacy_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        calls.append((action.name, action.path))
        if action.name == "list_dir" and action.path == "entities":
            return {"ok": True, "detail": "User.json"}
        if action.name == "list_dir" and action.path == "src/app/(app)/dashboard":
            return {"ok": True, "detail": "page.tsx"}
        if action.name == "read_file":
            return {"ok": True, "content": "export function CrudResource() {}"}
        raise AssertionError(f"unexpected action: {action}")

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_list_dir",
        lambda *args, **kwargs: pytest.fail("legacy list_dir must stay unused"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_read_file",
        lambda *args, **kwargs: pytest.fail("legacy read_file must stay unused"),
    )

    parts = await messages._build_agent_seed_parts(
        uuid4(),
        "max-cell",
        project_cell_handle=SimpleNamespace(execute=fake_execute),
    )

    assert calls == [
        ("list_dir", "entities"),
        ("list_dir", "src/app/(app)/dashboard"),
        ("read_file", "src/components/omnia/crud-resource.tsx"),
    ]
    assert parts == [
        "entities/ contains:\nUser.json",
        "src/app/(app)/dashboard/ contains:\npage.tsx",
        (
            "src/components/omnia/crud-resource.tsx (the entity-page "
            'component — render <CrudResource entity="Name"/> in each '
            "page):\nexport function CrudResource() {}"
        ),
    ]


@pytest.mark.asyncio
async def test_project_cell_build_routes_through_handle_without_legacy_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        calls.append(action.name)
        return {"ok": True, "detail": "build ok"}

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_build",
        lambda *args, **kwargs: pytest.fail("legacy build must stay unused"),
    )

    result = await messages._project_cell_build(SimpleNamespace(execute=fake_execute))

    assert result == {"ok": True, "detail": "build ok"}
    assert calls == ["build"]


@pytest.mark.asyncio
async def test_project_cell_runtime_check_routes_through_handle_without_legacy_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        calls.append((action.name, action.path))
        return {"ok": True, "detail": "runtime ok"}

    monkeypatch.setattr(
        messages.orchestrator_client,
        "runtime_status",
        lambda *args, **kwargs: pytest.fail("legacy runtime_status must stay unused"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "get_status",
        lambda *args, **kwargs: pytest.fail("legacy get_status must stay unused"),
    )

    result = await messages._project_cell_runtime_check(
        SimpleNamespace(execute=fake_execute),
        path="/",
    )

    assert result == {"ok": True, "detail": "runtime ok"}
    assert calls == [("runtime_check", "/")]


@pytest.mark.asyncio
async def test_apply_project_cell_preview_files_mirrors_cell_before_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    preview_calls: list[tuple[str, dict[str, str], tuple[str, ...]]] = []

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        return SimpleNamespace(generated_files={}, failure=None)

    async def fake_hot_reload(project_id, slug, files, *, empty_files=()):
        preview_calls.append((slug, dict(files), tuple(empty_files)))
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", fake_hot_reload)

    await messages._apply_project_cell_preview_files(
        project_id=uuid4(),
        project_slug="max-cell",
        files={"src/app/page.tsx": "v2\n", "obsolete.txt": ""},
        project_cell_handle=SimpleNamespace(
            stage_patch=fake_stage_patch,
            sync_preview=fake_sync_preview,
        ),
    )

    assert stage_calls == [({"src/app/page.tsx": "v2\n"}, ("obsolete.txt",))]
    assert preview_calls == []


@pytest.mark.asyncio
async def test_apply_project_cell_preview_files_preserves_explicit_empty_files_in_legacy_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_calls: list[tuple[str, dict[str, str], tuple[str, ...]]] = []

    async def fake_hot_reload(project_id, slug, files, *, empty_files=()):
        preview_calls.append((slug, dict(files), tuple(empty_files)))
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", fake_hot_reload)

    await messages._apply_project_cell_preview_files(
        project_id=uuid4(),
        project_slug="max-cell",
        files={"empty.txt": "", "deleted.txt": ""},
        empty_files=("empty.txt",),
    )

    assert preview_calls == [
        (
            "max-cell",
            {"empty.txt": "", "deleted.txt": ""},
            ("empty.txt",),
        )
    ]


@pytest.mark.asyncio
async def test_abort_unsafe_max_backend_restores_cell_and_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    preview_calls: list[str] = []
    files = {
        "src/app/page.tsx": "unsafe page\n",
        "src/app/api/max/route.ts": "unsafe route\n",
    }

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        preview_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("direct hot_reload should not run with a cell handle"),
    )

    with pytest.raises(ApiError) as caught:
        await messages._abort_unsafe_max_backend(
            project_id=uuid4(),
            project_slug="max-cell",
            current_files={"src/app/page.tsx": "safe page\n"},
            files=files,
            unsafe_paths=["src/app/api/max/route.ts"],
            project_cell_handle=SimpleNamespace(
                stage_patch=fake_stage_patch,
                sync_preview=fake_sync_preview,
            ),
        )

    assert caught.value.status_code == 422
    assert files == {
        "src/app/page.tsx": "safe page\n",
        "src/app/api/max/route.ts": "",
    }
    assert stage_calls == [({"src/app/page.tsx": "safe page\n"}, ("src/app/api/max/route.ts",))]
    assert preview_calls == ["sync"]


def test_max_shell_kill_switch_allows_owner_cell_without_attestation() -> None:
    assert (
        messages._resolve_max_shell_enabled(
            max_shell_requested=False,
            sandbox_attested=True,
            project_cell_handle=SimpleNamespace(),
        )
        is False
    )
    assert (
        messages._resolve_max_shell_enabled(
            max_shell_requested=True,
            sandbox_attested=False,
            project_cell_handle=SimpleNamespace(),
        )
        is True
    )


@pytest.mark.asyncio
async def test_run_max_shell_action_uses_project_cell_executor_without_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_calls: list[str] = []
    sync_calls: list[str] = []

    async def fake_base_executor(action: Action) -> dict[str, object]:
        base_calls.append(str(action.args.get("cmd") or ""))
        return {
            "ok": True,
            "detail": "cell shell ok",
            "files": {"src/app/page.tsx": "v2\n"},
        }

    async def fake_snapshot_files() -> dict[str, str]:
        return {"src/app/page.tsx": "v1\n"}

    async def fake_sync_preview():
        sync_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_exec_sandbox",
        lambda *args, **kwargs: pytest.fail("sandbox path must stay unused for Project Cell"),
    )

    result = await messages._run_max_shell_action(
        action=Action(name="bash", args={"cmd": "pnpm test"}),
        project_id=uuid4(),
        project_slug="max-cell",
        max_shell_enabled=True,
        base_agent_executor=fake_base_executor,
        project_cell_handle=SimpleNamespace(
            snapshot_files=fake_snapshot_files,
            sync_preview=fake_sync_preview,
        ),
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {
        "ok": True,
        "detail": "cell shell ok\n\nProject Cell synced files: src/app/page.tsx",
        "files": {"src/app/page.tsx": "v2\n"},
    }
    assert base_calls == ["pnpm test"]
    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_run_max_shell_action_keeps_sandbox_for_non_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox_calls: list[str] = []
    hot_reload_calls: list[dict[str, str]] = []

    async def fake_sandbox(_project_id, _project_slug, cmd: str) -> dict[str, object]:
        sandbox_calls.append(cmd)
        return {
            "ok": True,
            "detail": "sandbox ok",
            "files": {"src/app/page.tsx": "v2\n"},
            "base_workspace_revision": "a" * 64,
        }

    async def fake_hot_reload(_project_id, _slug, files, *, base_workspace_revision=None):
        hot_reload_calls.append(dict(files))
        assert base_workspace_revision == "a" * 64
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "agent_exec_sandbox", fake_sandbox)
    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", fake_hot_reload)

    result = await messages._run_max_shell_action(
        action=Action(name="bash", args={"cmd": "pnpm test"}),
        project_id=uuid4(),
        project_slug="max-sandbox",
        max_shell_enabled=True,
        base_agent_executor=lambda _action: pytest.fail("cell executor must stay unused"),
        project_cell_handle=None,
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {
        "ok": True,
        "detail": "sandbox ok\n\nSandbox synced files: src/app/page.tsx",
        "files": {"src/app/page.tsx": "v2\n"},
    }
    assert sandbox_calls == ["pnpm test"]
    assert hot_reload_calls == [{"src/app/page.tsx": "v2\n"}]


@pytest.mark.asyncio
async def test_run_max_shell_action_rolls_back_rejected_cell_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    sync_calls: list[str] = []

    async def fake_base_executor(_action: Action) -> dict[str, object]:
        return {
            "ok": True,
            "detail": "cell shell ok",
            "files": {
                "src/app/page.tsx": "unsafe page\n",
                "src/app/api/max/route.ts": "unsafe route\n",
            },
        }

    async def fake_snapshot_files() -> dict[str, str]:
        return {"src/app/page.tsx": "safe page\n"}

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        sync_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_exec_sandbox",
        lambda *args, **kwargs: pytest.fail("sandbox path must stay unused for Project Cell"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("rollback must go through Project Cell helper"),
    )

    result = await messages._run_max_shell_action(
        action=Action(name="bash", args={"cmd": "pnpm test"}),
        project_id=uuid4(),
        project_slug="max-cell",
        max_shell_enabled=True,
        base_agent_executor=fake_base_executor,
        project_cell_handle=SimpleNamespace(
            snapshot_files=fake_snapshot_files,
            stage_patch=fake_stage_patch,
            sync_preview=fake_sync_preview,
        ),
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {
        "ok": False,
        "error": (
            "MAX Studio owns /api/max and /api/omnia. "
            "Shell changes there are blocked; use the "
            "managed integration client instead."
        ),
    }
    assert stage_calls == [({"src/app/page.tsx": "safe page\n"}, ("src/app/api/max/route.ts",))]
    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_rollback_project_cell_shell_files_preserves_zero_byte_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    sync_calls: list[str] = []

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        sync_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("rollback must stay on Project Cell path"),
    )

    rolled_back = await messages._rollback_project_cell_shell_files(
        project_id=uuid4(),
        project_slug="max-cell",
        snapshot_files={"empty.txt": ""},
        touched_paths=("empty.txt", "removed.txt"),
        project_cell_handle=SimpleNamespace(
            stage_patch=fake_stage_patch,
            sync_preview=fake_sync_preview,
        ),
    )

    assert rolled_back is False
    assert stage_calls == [({"empty.txt": ""}, ("removed.txt",))]
    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_run_app_self_repair_uses_project_cell_preview_apply_without_hot_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_calls: list[dict[str, str]] = []
    probe_calls: list[object] = []

    async def fake_probe_app_error(*args, **kwargs):
        probe_calls.append(kwargs.get("project_cell_handle"))
        if len(probe_calls) == 1:
            return {"error": "boom", "file": "src/app/page.tsx"}, "compile"
        return None, ""

    async def fake_apply_preview_files(**kwargs) -> None:
        assert kwargs["project_cell_handle"] is not None
        apply_calls.append(dict(kwargs["files"]))

    async def fake_propose_fix(**kwargs) -> dict[str, str]:
        assert kwargs["file_path"] == "src/app/page.tsx"
        return {"src/app/page.tsx": "fixed\n"}

    monkeypatch.setattr(messages, "_probe_app_error", fake_probe_app_error)
    monkeypatch.setattr(messages, "_apply_project_cell_preview_files", fake_apply_preview_files)
    monkeypatch.setattr(messages.app_doctor, "propose_fix", fake_propose_fix)
    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("legacy hot_reload must stay unused"),
    )

    repaired, final_error, category = await messages._run_app_self_repair(
        project_id=uuid4(),
        slug="max-cell",
        files={"src/app/page.tsx": "broken\n"},
        passes=1,
        project_cell_handle=SimpleNamespace(),
    )

    assert repaired == {"src/app/page.tsx": "fixed\n"}
    assert final_error is None
    assert category == ""
    assert len(probe_calls) == 2
    assert apply_calls == [{"src/app/page.tsx": "fixed\n"}]


def test_hard_coverage_failure_is_captured_for_release_attestation() -> None:
    hard = SimpleNamespace(hard_missing=lambda: ["products"], passed=False, checks=[])
    soft = SimpleNamespace(hard_missing=lambda: [], passed=False, checks=[])

    capture = messages._capture_hard_coverage_attestation(None, hard, enabled=True)

    assert capture == [("coverage", hard)]
    assert messages._capture_hard_coverage_attestation(None, soft, enabled=True) is None
    assert messages._capture_hard_coverage_attestation(None, hard, enabled=False) is None
