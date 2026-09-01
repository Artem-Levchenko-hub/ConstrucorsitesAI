from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from omnia_api.core.config import get_settings
from omnia_api.models.base import Base
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services import project_cell_executor
from omnia_api.services.agent_builder import Action
from omnia_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    ProjectCellAgentExecResponse,
    ProjectCellAgentWorkspaceSnapshot,
    ProjectCellAgentWriteResponse,
    ProjectCellDraftApplyResponse,
    ProjectCellPreviewSession,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cell_control import ProjectCellControlReadiness

pytestmark = pytest.mark.asyncio
SET_UPDATED_AT_FN = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
"""


def _resolve_test_database_url() -> str:
    settings = get_settings()
    if settings.database_test_url:
        return settings.database_test_url
    base = settings.database_url.rsplit("/", 1)[0]
    return f"{base}/omnia_test"


async def test_dependency_reuse_policy_allows_matching_bundled_metadata() -> None:
    workspace_package = """
    {
      "name": "user-project",
      "scripts": {"typecheck": "tsc --noEmit", "lint": "eslint ."},
      "packageManager": "pnpm@10.0.0",
      "dependencies": {"next": "15.0.0", "react": "19.0.0"},
      "devDependencies": {"typescript": "5.6.0"},
      "pnpm": {"onlyBuiltDependencies": ["sharp"]}
    }
    """
    bundled_package = """
    {
      "name": "bundled-template",
      "packageManager": "pnpm@10.0.0",
      "dependencies": {"react": "19.0.0", "next": "15.0.0"},
      "devDependencies": {"typescript": "5.6.0"},
      "pnpm": {"onlyBuiltDependencies": ["sharp"]}
    }
    """

    error = project_cell_executor._project_cell_dependency_reuse_error(
        workspace_package_json=workspace_package,
        bundled_package_json=bundled_package,
        workspace_lockfile="lockfileVersion: '9.0'\n",
        bundled_lockfile="lockfileVersion: '9.0'\n",
    )

    assert error is None


@pytest.mark.parametrize(
    ("workspace_package", "workspace_lockfile", "bundled_lockfile", "expected"),
    [
        (
            """
            {
              "packageManager": "pnpm@10.0.0",
              "dependencies": {"next": "15.1.0"}
            }
            """,
            "lockfileVersion: '9.0'\n",
            "lockfileVersion: '9.0'\n",
            "dependency metadata differs",
        ),
        (
            """
            {
              "packageManager": "pnpm@10.0.0",
              "dependencies": {"next": "15.0.0"}
            }
            """,
            None,
            "lockfileVersion: '9.0'\n",
            "pnpm-lock.yaml presence differs",
        ),
        (
            """
            {
              "packageManager": "pnpm@10.0.0",
              "dependencies": {"next": "15.0.0"}
            }
            """,
            "lockfileVersion: '9.1'\n",
            "lockfileVersion: '9.0'\n",
            "pnpm-lock.yaml differs",
        ),
    ],
)
async def test_dependency_reuse_policy_rejects_mismatch(
    workspace_package: str,
    workspace_lockfile: str | None,
    bundled_lockfile: str | None,
    expected: str,
) -> None:
    error = project_cell_executor._project_cell_dependency_reuse_error(
        workspace_package_json=workspace_package,
        bundled_package_json="""
        {
          "packageManager": "pnpm@10.0.0",
          "dependencies": {"next": "15.0.0"}
        }
        """,
        workspace_lockfile=workspace_lockfile,
        bundled_lockfile=bundled_lockfile,
    )

    assert error is not None
    assert expected in error


@pytest.mark.parametrize(
    ("package_json_text", "label", "expected"),
    [
        ("{", "workspace", "workspace package.json is invalid JSON"),
        ("[]", "workspace", "workspace package.json must contain a JSON object"),
        (
            '{"packageManager": 1}',
            "bundled image",
            "bundled image package.json packageManager must be a string",
        ),
        (
            '{"dependencies": []}',
            "workspace",
            "workspace package.json dependencies must be an object",
        ),
    ],
)
async def test_dependency_metadata_guards_validate_shape(
    package_json_text: str,
    label: str,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        project_cell_executor._normalize_project_cell_dependency_metadata(
            package_json_text,
            label=label,
        )


async def test_project_cell_build_command_uses_bundled_dependency_guard() -> None:
    command = project_cell_executor._PROJECT_CELL_BUILD_CMD

    assert "pnpm install" not in command
    assert "/app/package.json" in command
    assert "/app/pnpm-lock.yaml" in command
    assert "cannot safely reuse bundled node_modules" in command
    assert "pnpm db:push" in command
    assert "pnpm typecheck" in command


@pytest_asyncio.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    test_url = _resolve_test_database_url()
    base_url, db_name = test_url.rsplit("/", 1)
    admin_url = f"{base_url}/postgres"
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = (
            await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            )
        ).scalar()
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()

    engine = create_async_engine(test_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text(SET_UPDATED_AT_FN))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(
        email=f"cell-exec-{label}-{uuid4().hex}@example.com",
        password_hash="x",
        is_anon=False,
        status="active",
        email_verified_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def _new_project(
    session: AsyncSession,
    owner: User,
    *,
    label: str = "project",
    template: str = "max_miniapp",
) -> Project:
    project = Project(
        owner_id=owner.id,
        name=f"Project Cell {label}",
        slug=f"cell-exec-{label}-{uuid4().hex}",
        template=template,
    )
    session.add(project)
    await session.flush()
    return project


async def _new_run(
    session: AsyncSession,
    project: Project,
    user: User,
    *,
    label: str = "run",
    status: str = "pending",
) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        user_id=user.id,
        idempotency_key=f"cell-exec-{label}-{uuid4().hex}",
        prompt_hash="hash",
        status=status,
    )
    session.add(run)
    await session.flush()
    return run


@dataclass(slots=True)
class _ExecutorHarness:
    handle: project_cell_executor.ProjectCellExecutorHandle
    workspace_id: UUID
    project_id: UUID
    project_slug: str
    run_id: UUID
    write_calls: list[dict[str, object]]
    exec_calls: list[dict[str, object]]
    hot_reload_calls: list[dict[str, str]]
    hot_reload_empty_files: list[tuple[str, ...]]
    legacy_actions: list[str]


async def _prepare_executor(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    *,
    snapshot_files: dict[str, str] | None = None,
    cell_exec_files: dict[str, str] | None = None,
    cell_exec_result: dict[str, object] | None = None,
    hot_reload_result: dict[str, object] | None = None,
    hot_reload_results: list[dict[str, object]] | None = None,
) -> _ExecutorHarness:
    owner = await _new_user(db_session, "owner")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, project, owner)
    await db_session.commit()
    expected_project_id = project.id
    expected_project_slug = project.slug
    expected_run_id = run.id

    snapshot_files = snapshot_files or {}
    cell_files = dict(snapshot_files)
    cell_exec_files = cell_exec_files or {}
    exec_calls: list[dict[str, object]] = []
    hot_reload_calls: list[dict[str, str]] = []
    hot_reload_empty_files: list[tuple[str, ...]] = []
    write_calls: list[dict[str, object]] = []
    legacy_actions: list[str] = []
    revision_number = 1

    def _current_revision() -> str:
        return f"{revision_number:064x}"

    def _bump_revision() -> str:
        nonlocal revision_number
        revision_number += 1
        return _current_revision()

    async def ready_readiness(_user, _project_id):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        project_cell_executor,
        "inspect_project_cell_control",
        ready_readiness,
    )

    async def fake_execute_cell_operation(_session_factory, operation_id, _client):
        workspace = await db_session.scalar(
            select(ProjectCellWorkspace).where(
                ProjectCellWorkspace.generation_run_id == expected_run_id
            )
        )
        assert workspace is not None
        workspace.fencing_epoch = 1
        await db_session.commit()
        return SimpleNamespace(
            operation_id=operation_id,
            status="completed",
            response=ProjectCellResourceResponse(
                workspace_id=workspace.id,
                state="resources_ready",
                provider_ref="cell-1",
                fencing_epoch=1,
                checkpoint_ref=None,
                has_workspace=True,
                has_agent_home=True,
                has_postgres=True,
                has_redis=True,
            ),
        )

    async def fake_bootstrap(
        workspace_id: UUID,
        *,
        generation_run_id: UUID | None,
        fencing_epoch: int,
    ) -> ProjectCellAgentWorkspaceSnapshot:
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        return ProjectCellAgentWorkspaceSnapshot(
            files=dict(cell_files),
            seeded_from_project=not bool(cell_files),
            generation_run_id=expected_run_id,
            fencing_epoch=1,
            workspace_revision=_current_revision(),
        )

    async def fake_write_files(
        workspace_id: UUID,
        *,
        generation_run_id: UUID | None,
        fencing_epoch: int,
        expected_revision: str,
        files: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> ProjectCellAgentWriteResponse:
        assert workspace_id
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        assert expected_revision == _current_revision()
        write_calls.append(
            {
                "generation_run_id": generation_run_id,
                "fencing_epoch": fencing_epoch,
                "expected_revision": expected_revision,
                "files": dict(files),
                "deletes": list(deletes),
            }
        )
        for path, content in files.items():
            cell_files[path] = content
        for path in deletes:
            cell_files.pop(path, None)
        return ProjectCellAgentWriteResponse(
            written=len(files),
            deleted=len(deletes),
            workspace_revision=_bump_revision(),
        )

    async def fake_exec(
        workspace_id: UUID,
        cmd: str,
        *,
        generation_run_id: UUID | None,
        fencing_epoch: int,
        expected_revision: str,
        timeout_seconds: int = 180,
    ) -> ProjectCellAgentExecResponse:
        assert workspace_id
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        assert expected_revision == _current_revision()
        exec_calls.append(
            {
                "cmd": cmd,
                "timeout_seconds": timeout_seconds,
                "generation_run_id": generation_run_id,
                "fencing_epoch": fencing_epoch,
                "expected_revision": expected_revision,
            }
        )
        for path, content in cell_exec_files.items():
            if content == "":
                cell_files.pop(path, None)
            else:
                cell_files[path] = content
        if cell_exec_files:
            _bump_revision()
        payload = dict(
            cell_exec_result
            or {"ok": True, "exit_code": 0, "detail": "cell exec ok", "timed_out": False}
        )
        return ProjectCellAgentExecResponse(
            ok=bool(payload["ok"]),
            exit_code=int(payload["exit_code"]),
            detail=str(payload["detail"]),
            timed_out=bool(payload["timed_out"]),
            workspace_revision=_current_revision(),
        )

    async def fake_hot_reload(
        workspace_id: UUID,
        *,
        generation_run_id: UUID,
        fencing_epoch: int,
        expected_revision: str,
        files: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> ProjectCellDraftApplyResponse:
        assert workspace_id
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        assert expected_revision == _current_revision()
        hot_reload_calls.append(dict(files))
        hot_reload_empty_files.append(tuple(path for path, value in files.items() if value == ""))
        cell_files.update(files)
        for path in deletes:
            cell_files.pop(path, None)
        if hot_reload_results:
            result = dict(hot_reload_results.pop(0))
        else:
            result = dict(hot_reload_result or {})
        lockfile = result.pop("pnpm_lockfile", None)
        if isinstance(lockfile, str):
            cell_files["pnpm-lock.yaml"] = lockfile
            _bump_revision()
        draft_state = (
            "draft_failed"
            if result.get("migration_exit_code") not in {None, 0}
            else "draft_running"
        )
        return ProjectCellDraftApplyResponse.from_json({
            **result, "state": draft_state, "workspace_revision": _current_revision(),
            "preview_url": "https://cell.preview.example.test",
        })

    async def fake_preview(workspace_id: UUID, *, generation_run_id: UUID, fencing_epoch: int):
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
        return ProjectCellPreviewSession(
            workspace_id=workspace_id,
            preview_url=origin,
            bootstrap_url=f"{origin}/api/omnia/preview-session"
            "?expires=1893456000&signature=" + "a" * 43,
            expires_at="2030-01-01T00:00:00+00:00",
        )

    async def legacy_execute(action: Action) -> dict[str, object]:
        legacy_actions.append(action.name)
        return {"ok": True, "detail": f"legacy:{action.name}"}

    monkeypatch.setattr(
        project_cell_executor,
        "execute_cell_operation",
        fake_execute_cell_operation,
    )
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_agent_bootstrap",
        fake_bootstrap,
    )
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_agent_write_files",
        fake_write_files,
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", fake_exec)
    monkeypatch.setattr(project_cell_executor, "project_cell_apply_draft", fake_hot_reload)
    monkeypatch.setattr(project_cell_executor, "project_cell_create_preview_session", fake_preview)
    from omnia_api.services import orchestrator_client

    def forbidden_legacy(*args, **kwargs):
        pytest.fail("selected cell must never call a legacy runtime")

    monkeypatch.setattr(orchestrator_client, "hot_reload", forbidden_legacy)
    monkeypatch.setattr(orchestrator_client, "create_max_preview_session", forbidden_legacy)

    handle = await project_cell_executor.maybe_create_project_cell_executor(
        project_id=expected_project_id,
        project_slug=expected_project_slug,
        project_template="max_miniapp",
        user_id=owner.id,
        generation_run_id=expected_run_id,
        legacy_execute=legacy_execute,
    )
    assert handle is not None

    project_id = expected_project_id
    project_slug = expected_project_slug
    run_id = expected_run_id
    db_session.expire_all()
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.project_id == project_id)
    )
    assert workspace is not None

    return _ExecutorHarness(
        handle=handle,
        workspace_id=workspace.id,
        project_id=project_id,
        project_slug=project_slug,
        run_id=run_id,
        write_calls=write_calls,
        exec_calls=exec_calls,
        hot_reload_calls=hot_reload_calls,
        hot_reload_empty_files=hot_reload_empty_files,
        legacy_actions=legacy_actions,
    )


async def test_non_max_templates_skip_project_cell_executor() -> None:
    handle = await project_cell_executor.maybe_create_project_cell_executor(
        project_id=uuid4(),
        project_slug="plain-app",
        project_template="blank",
        user_id=uuid4(),
        generation_run_id=uuid4(),
        legacy_execute=lambda _action: None,  # type: ignore[arg-type]
    )

    assert handle is None


async def test_ready_owner_executor_bootstraps_workspace_and_syncs_preview(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={
            "src/app/page.tsx": "export default function Page(){return null}\n",
        },
        cell_exec_files={
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        },
        cell_exec_result={"ok": True, "exit_code": 0, "detail": "cell:build", "timed_out": False},
    )

    read = await harness.handle.execute(
        Action(name="read_file", args={"path": "src/app/page.tsx"})
    )
    listed = await harness.handle.execute(Action(name="list_dir", args={"path": "src/app"}))
    grep = await harness.handle.execute(
        Action(name="grep", args={"pattern": "Page", "path": "src"})
    )
    write = await harness.handle.execute(
        Action(
            name="write_file",
            args={"path": "src/app/lib.ts", "content": "export const x = 1\n"},
        )
    )
    build = await harness.handle.execute(Action(name="build", args={}))
    lockfile = await harness.handle.execute(
        Action(name="read_file", args={"path": "pnpm-lock.yaml"})
    )

    assert read == {
        "ok": True,
        "content": "export default function Page(){return null}\n",
    }
    assert listed == {"ok": True, "detail": "page.tsx"}
    assert grep == {
        "ok": True,
        "detail": "src/app/page.tsx:1:export default function Page(){return null}",
    }
    assert write["ok"] is True
    assert build == {"ok": True, "detail": "cell:build"}
    assert lockfile == {"ok": True, "content": "lockfileVersion: '9.0'\n"}
    assert await harness.handle.export_files() == {
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "src/app/lib.ts": "export const x = 1\n",
    }
    assert harness.write_calls == [
        {
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{1:064x}",
            "files": {"src/app/lib.ts": "export const x = 1\n"},
            "deletes": [],
        }
    ]
    assert harness.exec_calls == [
        {
            "cmd": project_cell_executor._PROJECT_CELL_BUILD_CMD,
            "timeout_seconds": 600,
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{2:064x}",
        }
    ]
    assert harness.hot_reload_calls == []
    assert harness.legacy_actions == []

    workspace = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert workspace is not None
    assert workspace.state == "ready"
    assert workspace.provider_ref == "cell-1"
    assert workspace.generation_run_id == harness.run_id
    operation = await db_session.scalar(
        select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == harness.workspace_id
        )
    )
    assert operation is not None
    assert operation.kind == "ensure"


async def test_apply_external_files_updates_local_state_without_extra_sync(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={"obsolete.txt": "remove me\n"},
    )

    await harness.handle.apply_external_files(
        {
            "src/app/api.ts": "export const api = true\n",
            "obsolete.txt": "",
        }
    )
    added = await harness.handle.execute(
        Action(name="read_file", args={"path": "src/app/api.ts"})
    )
    removed = await harness.handle.execute(Action(name="read_file", args={"path": "obsolete.txt"}))
    build = await harness.handle.execute(Action(name="build", args={}))

    assert added == {"ok": True, "content": "export const api = true\n"}
    assert removed == {"ok": False, "error": "not found: obsolete.txt"}
    assert build == {"ok": True, "detail": "cell exec ok"}
    assert harness.write_calls == [
        {
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{1:064x}",
            "files": {"src/app/api.ts": "export const api = true\n"},
            "deletes": ["obsolete.txt"],
        }
    ]
    assert harness.exec_calls == [
        {
            "cmd": project_cell_executor._PROJECT_CELL_BUILD_CMD,
            "timeout_seconds": 600,
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{2:064x}",
        }
    ]
    assert harness.hot_reload_calls == []
    assert harness.legacy_actions == []


async def test_sync_failure_blocks_runtime_actions_before_legacy_executor(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        hot_reload_result={
            "state": "hot_reloaded",
            "package_exit_code": 1,
            "package_stderr_tail": "boom",
        },
    )

    await harness.handle.execute(
        Action(name="write_file", args={"path": "src/app/page.tsx", "content": "v1\n"})
    )
    result = await harness.handle.execute(Action(name="runtime_check", args={"path": "/"}))

    assert result == {
        "ok": False,
        "error": "runtime apply failed during Project Cell sync: package_exit_code=1: boom",
    }
    assert harness.hot_reload_calls == [{"src/app/page.tsx": "v1\n"}]
    assert harness.legacy_actions == []


async def test_sync_failure_stays_dirty_and_retries_same_diff(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        hot_reload_results=[
            {
                "state": "hot_reloaded",
                "package_exit_code": 1,
                "package_stderr_tail": "boom",
            },
            {"state": "hot_reloaded"},
        ],
    )

    await harness.handle.execute(
        Action(name="write_file", args={"path": "src/app/page.tsx", "content": "v1\n"})
    )
    failed = await harness.handle.execute(Action(name="read_logs", args={}))
    succeeded = await harness.handle.execute(Action(name="read_logs", args={}))

    assert failed == {
        "ok": False,
        "error": "runtime apply failed during Project Cell sync: package_exit_code=1: boom",
    }
    assert succeeded == {"ok": True, "detail": "(no logs yet)"}
    assert harness.hot_reload_calls == [
        {"src/app/page.tsx": "v1\n"},
        {"src/app/page.tsx": "v1\n"},
    ]
    assert harness.legacy_actions == []


async def test_write_file_preserves_zero_byte_file_in_cell(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)

    write = await harness.handle.execute(
        Action(name="write_file", args={"path": "empty.txt", "content": ""})
    )
    read = await harness.handle.execute(Action(name="read_file", args={"path": "empty.txt"}))

    assert write == {
        "ok": True,
        "content": "",
        "detail": "wrote empty.txt (0 bytes)",
    }
    assert read == {"ok": True, "content": ""}
    assert harness.write_calls == [
        {
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{1:064x}",
            "files": {"empty.txt": ""},
            "deletes": [],
        }
    ]


async def test_sync_preview_marks_zero_byte_files_as_explicit_empty_paths(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)

    await harness.handle.stage_patch({"blank.txt": ""})
    sync = await harness.handle.sync_preview()

    assert sync.generated_files == {}
    assert sync.failure is None
    assert harness.hot_reload_calls == [{"blank.txt": ""}]
    assert harness.hot_reload_empty_files == [("blank.txt",)]


async def test_cell_preview_starts_without_edits_and_refreshes_generated_files(
    monkeypatch, db_session, test_engine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine,
        hot_reload_result={"pnpm_lockfile": "generated lock", "runtime_log_tail": "cell ready"},
    )
    result = await harness.handle.execute(Action(name="read_logs", args={}))
    assert result == {
        "ok": True, "detail": "cell ready", "files": {"pnpm-lock.yaml": "generated lock"},
    }
    assert harness.hot_reload_calls == [{}]
    assert harness.write_calls == []  # Generated files already belong to the cell volume.
    assert await harness.handle.snapshot_files() == {"pnpm-lock.yaml": "generated lock"}
    assert harness.legacy_actions == []


async def test_cell_migration_failure_cannot_be_reported_as_ready(
    monkeypatch, db_session, test_engine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine,
        hot_reload_result={"migration_exit_code": 1, "migration_stderr_tail": "schema conflict"},
    )
    with pytest.raises(
        project_cell_executor.ProjectCellExecutorUnavailable, match="schema conflict",
    ):
        await harness.handle.create_preview_session()
    assert harness.legacy_actions == []


async def test_cell_runtime_check_uses_workspace_session_not_legacy(
    monkeypatch, db_session, test_engine,
) -> None:
    from omnia_api.services import max_runtime_probe

    harness = await _prepare_executor(monkeypatch, db_session, test_engine)

    async def fake_probe(preview, *, path):
        assert preview.workspace_id == harness.workspace_id
        assert path == "/products"
        return max_runtime_probe.MaxRuntimeProbe(True, "cell database verified")

    monkeypatch.setattr(max_runtime_probe, "probe_max_cell_runtime", fake_probe)
    result = await harness.handle.execute(Action(name="runtime_check", args={"path": "/products"}))
    assert result == {"ok": True, "detail": "cell database verified"}
    assert harness.legacy_actions == []


async def test_ready_mark_cannot_steal_a_newer_workspace_lease(
    monkeypatch, db_session, test_engine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    project = await db_session.get(Project, harness.project_id)
    assert project is not None
    owner = await db_session.get(User, project.owner_id)
    assert owner is not None
    old_run = await db_session.get(GenerationRun, harness.run_id)
    assert old_run is not None
    old_run.status = "failed"
    await db_session.flush()
    newer_run = await _new_run(db_session, project, owner, label="newer")
    newer_run_id = newer_run.id
    workspace = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert workspace is not None
    workspace.generation_run_id = newer_run.id
    workspace.fencing_epoch = 2
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    with pytest.raises(
        project_cell_executor.ProjectCellExecutorUnavailable, match="lease changed",
    ):
        await project_cell_executor._mark_workspace_ready(
            session_factory=factory, workspace_id=harness.workspace_id,
            generation_run_id=harness.run_id, provider_ref="stale", fencing_epoch=1,
        )
    db_session.expire_all()
    preserved = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert preserved is not None
    assert preserved.generation_run_id == newer_run_id
    assert preserved.fencing_epoch == 2
    assert preserved.provider_ref == "cell-1"


async def test_ready_mark_reloads_cancellation_from_another_session(
    monkeypatch, db_session, test_engine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    workspace = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert workspace is not None
    workspace.state = "provisioning"
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as cancel_session:
        run = await cancel_session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == harness.run_id)
            .with_for_update()
        )
        assert run is not None
        run.status = "cancel_requested"
        activation = asyncio.create_task(
            project_cell_executor._mark_workspace_ready(
                session_factory=factory, workspace_id=harness.workspace_id,
                generation_run_id=harness.run_id, provider_ref="stale",
                fencing_epoch=1,
            )
        )
        await asyncio.sleep(0.2)
        assert not activation.done(), "activation did not wait for the run-row fence"
        await cancel_session.commit()
        with pytest.raises(
            project_cell_executor.ProjectCellExecutorUnavailable, match="lease changed",
        ):
            await activation
    db_session.expire_all()
    preserved = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert preserved is not None
    assert preserved.state == "provisioning"
    assert preserved.provider_ref == "cell-1"


async def test_stage_patch_preserves_zero_byte_file_and_explicit_delete(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={"remove.txt": "gone soon\n"},
    )

    await harness.handle.stage_patch(
        {"blank.txt": "", "keep.txt": "after\n"},
        ("remove.txt",),
    )
    blank = await harness.handle.execute(Action(name="read_file", args={"path": "blank.txt"}))
    kept = await harness.handle.execute(Action(name="read_file", args={"path": "keep.txt"}))
    removed = await harness.handle.execute(Action(name="read_file", args={"path": "remove.txt"}))

    assert blank == {"ok": True, "content": ""}
    assert kept == {"ok": True, "content": "after\n"}
    assert removed == {"ok": False, "error": "not found: remove.txt"}
    assert harness.write_calls == [
        {
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{1:064x}",
            "files": {"blank.txt": "", "keep.txt": "after\n"},
            "deletes": ["remove.txt"],
        }
    ]


async def test_bash_runs_inside_cell_and_returns_remote_diff(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={"src/app/page.tsx": "v1\n", "obsolete.txt": "old\n"},
        cell_exec_files={
            "src/app/page.tsx": "v2\n",
            "src/app/api.ts": "export const api = true\n",
            "obsolete.txt": "",
        },
        cell_exec_result={
            "ok": False,
            "exit_code": 1,
            "detail": "tests failed",
            "timed_out": False,
        },
    )

    result = await harness.handle.execute(
        Action(name="bash", args={"cmd": "pnpm test"})
    )

    assert result == {
        "ok": False,
        "detail": "tests failed",
        "files": {
            "src/app/page.tsx": "v2\n",
            "src/app/api.ts": "export const api = true\n",
            "obsolete.txt": "",
        },
    }
    assert await harness.handle.export_files() == {
        "src/app/page.tsx": "v2\n",
        "src/app/api.ts": "export const api = true\n",
        "obsolete.txt": "",
    }
    assert harness.exec_calls == [
        {
            "cmd": "pnpm test",
            "timeout_seconds": 300,
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{1:064x}",
        }
    ]


async def test_selected_but_unready_project_cell_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner = await _new_user(db_session, "not-ready-owner")
    project = await _new_project(db_session, owner, label="not-ready")
    run = await _new_run(db_session, project, owner, label="not-ready")
    await db_session.commit()

    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)

    async def unready_readiness(_user, _project_id):
        return ProjectCellControlReadiness(
            selected=True,
            ready=False,
            provider="docker_owner_canary",
            reason="provider_unsupported",
        )

    monkeypatch.setattr(
        project_cell_executor,
        "inspect_project_cell_control",
        unready_readiness,
    )

    with pytest.raises(project_cell_executor.ProjectCellExecutorUnavailable) as caught:
        await project_cell_executor.maybe_create_project_cell_executor(
            project_id=project.id,
            project_slug=project.slug,
            project_template="max_miniapp",
            user_id=owner.id,
            generation_run_id=run.id,
            legacy_execute=lambda _action: None,  # type: ignore[arg-type]
        )

    assert "Project Cell selected but not ready" in str(caught.value)


@pytest.mark.parametrize("mismatch", ["run_id", "fencing_epoch"])
async def test_bootstrap_rejects_mismatched_active_lease_before_ready(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    mismatch: str,
) -> None:
    owner = await _new_user(db_session, "lease-owner")
    project = await _new_project(db_session, owner, label="lease")
    run = await _new_run(db_session, project, owner, label="lease")
    await db_session.commit()
    run_id = run.id

    async def ready_readiness(_user, _project_id):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    async def fake_execute_cell_operation(_session_factory, operation_id, _client):
        workspace = await db_session.scalar(
            select(ProjectCellWorkspace).where(
                ProjectCellWorkspace.generation_run_id == run.id
            )
        )
        assert workspace is not None
        workspace.fencing_epoch = 1
        await db_session.commit()
        return SimpleNamespace(
            operation_id=operation_id,
            status="completed",
            response=ProjectCellResourceResponse(
                workspace_id=workspace.id,
                state="resources_ready",
                provider_ref="cell-1",
                fencing_epoch=1,
                checkpoint_ref=None,
                has_workspace=True,
                has_agent_home=True,
                has_postgres=True,
                has_redis=True,
            ),
        )

    async def fake_bootstrap(
        _workspace_id: UUID,
        *,
        generation_run_id: UUID | None,
        fencing_epoch: int,
    ) -> ProjectCellAgentWorkspaceSnapshot:
        assert generation_run_id == run.id
        assert fencing_epoch == 1
        return ProjectCellAgentWorkspaceSnapshot(
            files={},
            seeded_from_project=True,
            generation_run_id=uuid4() if mismatch == "run_id" else run.id,
            fencing_epoch=2 if mismatch == "fencing_epoch" else 1,
            workspace_revision=f"{1:064x}",
        )

    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", ready_readiness)
    monkeypatch.setattr(
        project_cell_executor,
        "execute_cell_operation",
        fake_execute_cell_operation,
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_bootstrap", fake_bootstrap)

    with pytest.raises(project_cell_executor.ProjectCellExecutorUnavailable) as caught:
        await project_cell_executor.maybe_create_project_cell_executor(
            project_id=project.id,
            project_slug=project.slug,
            project_template="max_miniapp",
            user_id=owner.id,
            generation_run_id=run.id,
            legacy_execute=lambda _action: None,  # type: ignore[arg-type]
        )

    assert "active lease does not match the run" in str(caught.value)
    db_session.expire_all()
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(
            ProjectCellWorkspace.generation_run_id == run_id
        )
    )
    assert workspace is not None
    assert workspace.state != "ready"


async def test_bootstrap_wraps_orchestrator_bad_request_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner = await _new_user(db_session, "bootstrap-bad-request-owner")
    project = await _new_project(db_session, owner, label="bootstrap-bad-request")
    run = await _new_run(db_session, project, owner, label="bootstrap-bad-request")
    await db_session.commit()

    async def ready_readiness(_user, _project_id):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    async def fake_execute_cell_operation(_session_factory, operation_id, _client):
        workspace = await db_session.scalar(
            select(ProjectCellWorkspace).where(
                ProjectCellWorkspace.generation_run_id == run.id
            )
        )
        assert workspace is not None
        return SimpleNamespace(
            operation_id=operation_id,
            status="completed",
            response=ProjectCellResourceResponse(
                workspace_id=workspace.id,
                state="resources_ready",
                provider_ref="cell-1",
                fencing_epoch=1,
                checkpoint_ref=None,
                has_workspace=True,
                has_agent_home=True,
                has_postgres=True,
                has_redis=True,
            ),
        )

    async def fake_bootstrap(
        _workspace_id: UUID,
        *,
        generation_run_id: UUID | None,
        fencing_epoch: int,
    ) -> ProjectCellAgentWorkspaceSnapshot:
        assert generation_run_id == run.id
        assert fencing_epoch == 1
        raise OrchestratorBadRequest(
            "Orchestrator rejected request: workspace generation lease mismatch",
            status_code=409,
            details={"effect_applied": False},
        )

    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", ready_readiness)
    monkeypatch.setattr(
        project_cell_executor,
        "execute_cell_operation",
        fake_execute_cell_operation,
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_bootstrap", fake_bootstrap)

    with pytest.raises(project_cell_executor.ProjectCellExecutorUnavailable) as caught:
        await project_cell_executor.maybe_create_project_cell_executor(
            project_id=project.id,
            project_slug=project.slug,
            project_template="max_miniapp",
            user_id=owner.id,
            generation_run_id=run.id,
            legacy_execute=lambda _action: None,  # type: ignore[arg-type]
        )

    assert "workspace generation lease mismatch" in str(caught.value)
