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


def test_resolve_max_shell_enabled_obeys_kill_switch_but_allows_owner_cell_without_attestation() -> None:
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
