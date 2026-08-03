from __future__ import annotations

from pathlib import Path

import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services import builder, deploy_state


def test_materialize_snapshot_removes_omitted_template_files(tmp_path: Path) -> None:
    (tmp_path / "src/app").mkdir(parents=True)
    (tmp_path / "src/app/page.tsx").write_text("starter", encoding="utf-8")
    (tmp_path / "src/app/obsolete.tsx").write_text("obsolete", encoding="utf-8")
    (tmp_path / "Dockerfile.prod").write_text("trusted", encoding="utf-8")

    builder._materialize_snapshot(
        tmp_path,
        {
            "src/app/page.tsx": "exact revision",
            "package.json": "{}",
            "Dockerfile.prod": "untrusted override",
        },
    )

    assert (tmp_path / "src/app/page.tsx").read_text(encoding="utf-8") == "exact revision"
    assert not (tmp_path / "src/app/obsolete.tsx").exists()
    assert (tmp_path / "Dockerfile.prod").read_text(encoding="utf-8") == "trusted"


def test_materialize_snapshot_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(OrchestratorError):
        builder._materialize_snapshot(tmp_path, {"../outside.ts": "bad"})


@pytest.mark.asyncio
async def test_exact_snapshot_deploy_does_not_require_live_dev_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(tmp_path / "deploy-state.json"))
    deploy_state.reset_for_tests()
    captured: dict[str, object] = {}

    async def fail_find(*_args: object, **_kwargs: object) -> str | None:
        raise AssertionError("exact deploy must not inspect a mutable dev container")

    async def fake_run(*args: object, **_kwargs: object) -> None:
        captured["args"] = args

    monkeypatch.setattr(builder.docker_client, "find_project_container", fail_find)
    monkeypatch.setattr(builder, "_run", fake_run)
    monkeypatch.setattr(builder.nginx_writer, "prod_url", lambda slug: f"https://{slug}.test")

    project_id = "11111111-2222-3333-4444-555555555555"
    commit_sha = "a" * 40
    record = await builder.start_deploy(
        project_id=project_id,
        slug="exact-app",
        commit_sha=commit_sha,
        template="max-miniapp-nextjs",
        source_files={"src/app/page.tsx": "exact"},
        idempotency_key="request-exact-1",
    )
    await builder._project_tasks[project_id]

    assert record.commit_sha == commit_sha
    args = captured["args"]
    assert isinstance(args, tuple)
    assert args[2] is None
    assert args[3] == "max-miniapp-nextjs"
    assert args[4] == {"src/app/page.tsx": "exact"}


@pytest.mark.asyncio
async def test_active_deploy_cannot_alias_another_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(tmp_path / "deploy-state.json"))
    deploy_state.reset_for_tests()
    project_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    deploy_state.start(
        project_id,
        idempotency_key="first",
        commit_sha="a" * 40,
    )

    with pytest.raises(OrchestratorError, match="another revision"):
        await builder.start_deploy(
            project_id=project_id,
            slug="exact-app",
            commit_sha="b" * 40,
            template="max-miniapp-nextjs",
            source_files={"src/app/page.tsx": "other revision"},
            idempotency_key="second",
        )


@pytest.mark.asyncio
async def test_active_same_revision_does_not_spawn_second_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(tmp_path / "deploy-state.json"))
    deploy_state.reset_for_tests()
    project_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    active = deploy_state.start(
        project_id,
        idempotency_key="first",
        commit_sha="a" * 40,
    )

    async def fail_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an active exact revision must keep its original task")

    monkeypatch.setattr(builder, "_run", fail_run)
    replayed = await builder.start_deploy(
        project_id=project_id,
        slug="exact-app",
        commit_sha="a" * 40,
        template="max-miniapp-nextjs",
        source_files={"src/app/page.tsx": "same revision"},
        idempotency_key="second",
    )

    assert replayed is active
