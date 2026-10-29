from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
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

from tests.test_generation_recovery_readers import install_healthy_reader_inventory
from yleum_api.core.config import get_settings
from yleum_api.models.base import Base
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.user import User
from yleum_api.routers import messages
from yleum_api.services import project_cell_capacity, project_cell_executor
from yleum_api.services.agent_builder import Action, run_agent_build
from yleum_api.services.generation import supervisor
from yleum_api.services.generation_runs import (
    finalize_generation_run,
    promote_generation_after_admission,
    write_capacity_dispatch_claim,
)
from yleum_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellAgentExecResponse,
    ProjectCellAgentWorkspaceSnapshot,
    ProjectCellAgentWriteResponse,
    ProjectCellCapacityRejection,
    ProjectCellCapacityWait,
    ProjectCellDraftApplyResponse,
    ProjectCellPreviewSession,
    ProjectCellResourceResponse,
    ProjectCellWorkspaceIdentity,
)
from yleum_api.services.project_cell_control import ProjectCellControlReadiness
from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity
from yleum_api.services.project_cells import (
    claim_cell_operation_committed,
    complete_cell_operation,
    mark_cell_operation_indeterminate,
    reserve_cell_operation,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def business_config_rpc(monkeypatch):
    seed = AsyncMock(return_value=False)
    monkeypatch.setattr(project_cell_executor, "project_cell_apply_business_config", seed)
    return seed


SET_UPDATED_AT_FN = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
"""


@pytest.mark.parametrize("tool,changed", [
    ("write_file", True), ("write_file", False), ("edit_file", True), ("edit_file", False),
])
async def test_file_mutation_receipt_witnesses_full_content_after_persist(
    monkeypatch, db_session, test_engine, tool, changed,
):
    path = "src/app/page.tsx"
    before = "v0" + "x" * 16_001
    after = "v1" + before[2:] if changed else before
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files={path: before},
    )
    args = {"path": "./src\\app/page.tsx"}
    if tool == "write_file":
        args["content"] = after
    else:
        args.update(search="v0", replace="v1" if changed else "v0")
    result = await harness.handle.execute(Action(name=tool, args=args))
    assert result["ok"] is True
    assert result["content_change"] == {
        "path": path,
        "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
        "after_sha256": hashlib.sha256(after.encode()).hexdigest(),
    }
    assert harness.write_calls[-1]["files"] == {path: after}


async def test_failed_persist_returns_no_content_change_receipt(
    monkeypatch, db_session, test_engine,
):
    path = "src/app/page.tsx"
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files={path: "v0"},
    )

    async def fail_persist(*args, **kwargs):
        raise OrchestratorUnavailable("persist failed")

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_write_files", fail_persist)
    result = await harness.handle.execute(Action(
        name="edit_file", args={"path": path, "search": "v0", "replace": "v1"},
    ))
    assert result["ok"] is False
    assert "content_change" not in result
    assert result.get("environment_mutated") is not False
    assert (await harness.handle.snapshot_files())[path] == "v0"


@pytest.mark.parametrize("args", [
    {"path": "src/app/page.tsx", "search": "same"},
    {"path": "src/app/missing.tsx", "search": "same", "replace": "new"},
    {"path": "src/app/page.tsx", "search": "absent", "replace": "new"},
    {"path": "src/app/page.tsx", "search": "same", "replace": "new"},
])
async def test_local_edit_rejection_proves_no_mutation_before_persistence(
    monkeypatch, db_session, test_engine, args,
):
    baseline = {"src/app/page.tsx": "same same"}
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files=baseline,
    )
    before_writes = len(harness.write_calls)
    result = await harness.handle.execute(Action(name="edit_file", args=args))
    assert result["ok"] is False
    assert result["environment_mutated"] is False
    assert "content_change" not in result and "files" not in result
    assert len(harness.write_calls) == before_writes
    assert await harness.handle.snapshot_files() == baseline


async def test_real_local_edit_rejection_preserves_checked_candidate_at_budget(
    monkeypatch, db_session, test_engine,
):
    path = "src/app/page.tsx"
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files={path: "v0"},
    )
    replies = iter([
        '<omnia:action name="edit_file">'
        '{"path":"src/app/page.tsx","search":"v0","replace":"v1"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="edit_file">'
        '{"path":"src/app/page.tsx","search":"absent","replace":"v2"}</omnia:action>',
    ])

    async def complete(*args, **kwargs):
        return next(replies)

    async def execute(action):
        if action.name == "build":
            return {"ok": True}
        return await harness.handle.execute(action)

    result = await run_agent_build(
        system_prompt="sys", user_prompt="repair", model="m", complete=complete,
        execute=execute, max_steps=3, edit_mode=True, coordinator_handoff=True,
        require_green_before_done=True,
    )
    assert result.done and result.needs_finalization
    assert result.stop_reason == "done_on_green"
    assert result.files == {path: "v1"}
    assert await harness.handle.snapshot_files() == {path: "v1"}
    assert len(harness.write_calls) == 1


async def test_new_file_receipt_distinguishes_absence_from_empty_content(
    monkeypatch, db_session, test_engine,
):
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    result = await harness.handle.execute(Action(
        name="write_file", args={"path": "new.txt", "content": ""},
    ))
    assert result["content_change"] == {
        "path": "new.txt", "before_sha256": None,
        "after_sha256": hashlib.sha256(b"").hexdigest(),
    }


async def test_real_executor_large_file_repair_does_not_hit_the_read_cycle_guard(
    monkeypatch, db_session, test_engine,
):
    path = "src/app/page.tsx"
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files={path: "v0" + "x" * 16_001},
    )
    replies = []
    for i in range(4):
        replies.append(f'<omnia:action name="read_file">{{"path":"{path}"}}</omnia:action>')
        if i < 3:
            payload = json.dumps({"path": path, "search": f"v{i}", "replace": f"v{i + 1}"})
            replies.append(f'<omnia:action name="edit_file">{payload}</omnia:action>')
            replies.append('<omnia:action name="build"></omnia:action>')
    replies.append('<omnia:action name="done"></omnia:action>')
    replies = iter(replies)

    async def complete(*args, **kwargs):
        return next(replies)

    async def execute(action):
        if action.name == "build":
            return {"ok": False, "detail": f"compiler failure {len(harness.write_calls)}"}
        return await harness.handle.execute(action)

    result = await run_agent_build(
        system_prompt="sys", user_prompt="repair", model="m", complete=complete,
        execute=execute, max_steps=11, edit_mode=True, ship_green_on_abort=False,
    )
    assert result.stop_reason == "done"
    assert result.files[path].startswith("v3")
    reads = [turn for turn in result.transcript
             if turn["content"].startswith("[observation: read_file")]
    assert len(reads) == 4


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


def _proof_identity() -> ProofIdentity:
    return ProofIdentity(
        workspace_id=UUID(int=1),
        generation_run_id=UUID(int=2),
        fencing_epoch=7,
        workspace_revision="1" * 64,
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64,
        toolchain_digest="6" * 64,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest="7" * 64,
    )


async def test_identity_invalidation_matrix_is_exact() -> None:
    initial = _proof_identity()

    assert project_cell_executor.invalidated_dimensions(initial, initial) == frozenset()
    assert project_cell_executor.invalidated_dimensions(
        initial,
        replace(initial, workspace_revision="8" * 64),
    ) == {
        ProofDimension.FAST_CHECK,
        ProofDimension.FULL_BUILD,
        ProofDimension.RUNTIME,
        ProofDimension.RELEASE,
    }
    assert project_cell_executor.invalidated_dimensions(
        initial,
        replace(initial, schema_data_digest="8" * 64),
    ) == {ProofDimension.RUNTIME, ProofDimension.RELEASE}
    assert project_cell_executor.invalidated_dimensions(
        initial,
        replace(initial, dependency_digest="8" * 64),
    ) == frozenset(ProofDimension)
    assert project_cell_executor.invalidated_dimensions(
        initial,
        replace(initial, fencing_epoch=8),
    ) == frozenset(ProofDimension)


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
    assert "pnpm typecheck" in command


async def test_project_cell_build_uses_migrations_before_typecheck() -> None:
    command = project_cell_executor._PROJECT_CELL_BUILD_CMD

    assert "pnpm db:push" not in command
    assert "drizzle-kit push" not in command
    assert "node scripts/apply-migrations.mjs" in command
    assert command.index("node scripts/apply-migrations.mjs") < command.index("pnpm typecheck")
    assert command.startswith("set -eu\n")


@pytest.mark.parametrize(
    "roles,expected",
    [
        (["bootstrap", "fast_check", "full_build"], "full_build"),
        (["bootstrap", "build", "test"], "build"),
        (["bootstrap", "build", "test", "full_build"], "full_build"),
        ([], "build"),
    ],
)
async def test_portable_build_uses_declared_modern_role_without_inventing_legacy_test(
    roles, expected,
):
    files = {".omnia/cell.json": json.dumps({
        "version": 1, "tasks": [{"name": role, "role": role} for role in roles],
    })}
    assert project_cell_executor._portable_build_role(files) == expected


@pytest.mark.parametrize("manifest", [None, "{", "[]", '{"tasks": "full_build"}'])
async def test_portable_build_role_rejects_missing_or_malformed_manifest(manifest):
    files = {} if manifest is None else {".omnia/cell.json": manifest}
    with pytest.raises(ValueError, match="Invalid portable machine manifest"):
        project_cell_executor._portable_build_role(files)


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
    write_workspaces: list[UUID]
    exec_calls: list[dict[str, object]]
    hot_reload_calls: list[dict[str, str]]
    hot_reload_empty_files: list[tuple[str, ...]]
    legacy_actions: list[str]
    adaptation_prepare_calls: list[dict[str, object]]
    adaptation_proof_calls: list[dict[str, object]]
    adaptation_cleanup_calls: list[dict[str, object]]


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
    capacity_dispatch_token: UUID | None = None,
    capabilities: dict[str, object] | None = None,
    restoration_adaptation: bool = False,
    lifecycle_client: object | None = None,
    expected_bootstrap_fence: int = 1,
) -> _ExecutorHarness:
    owner = await _new_user(db_session, "owner")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, project, owner)
    if restoration_adaptation:
        run.agent_state = {
            "restoration_adaptation": {
                "operation_id": str(uuid4()),
                "adaptation_run_id": str(run.id),
                "source_snapshot_id": str(uuid4()),
                "base_draft_snapshot_id": str(uuid4()),
                "source_commit_sha": "a" * 40,
                "sha256": "b" * 64,
            }
        }
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
    write_workspaces: list[UUID] = []
    legacy_actions: list[str] = []
    adaptation_prepare_calls: list[dict[str, object]] = []
    adaptation_proof_calls: list[dict[str, object]] = []
    adaptation_cleanup_calls: list[dict[str, object]] = []
    revision_number = 1

    def _current_revision() -> str:
        return f"{revision_number:064x}"

    def _bump_revision() -> str:
        nonlocal revision_number
        revision_number += 1
        return _current_revision()

    async def ready_readiness(_user, _project_id, **_selection):
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

    async def fake_execute_cell_operation(session_factory, operation_id, _client):
        claimed = await claim_cell_operation_committed(session_factory, operation_id)
        response = ProjectCellResourceResponse(
            workspace_id=claimed.workspace_id,
            state="resources_ready",
            provider_ref="cell-1",
            fencing_epoch=claimed.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )
        async with session_factory() as session:
            await complete_cell_operation(session, operation_id, response.to_wire_json())
            await session.commit()
        return SimpleNamespace(
            operation_id=operation_id,
            kind=claimed.kind,
            status="completed",
            response=response,
        )

    source_bootstrapped = False

    async def fake_bootstrap(
        workspace_id: UUID,
        *,
        generation_run_id: UUID | None,
        fencing_epoch: int,
    ) -> ProjectCellAgentWorkspaceSnapshot:
        nonlocal source_bootstrapped
        assert generation_run_id == expected_run_id
        assert fencing_epoch == expected_bootstrap_fence
        # Canonical metadata must exist before bootstrap can start source SSR.
        if not source_bootstrapped:
            seed = business_seed.await_args_list[0]
            assert seed.args == (workspace_id,)
            assert seed.kwargs["project_id"] == expected_project_id
            assert seed.kwargs["owner_id"] == owner.id
            assert seed.kwargs["generation_run_id"] == expected_run_id
            assert seed.kwargs["fencing_epoch"] == expected_bootstrap_fence
            source_bootstrapped = True
        return ProjectCellAgentWorkspaceSnapshot(
            files=dict(cell_files),
            seeded_from_project=not bool(cell_files),
            generation_run_id=expected_run_id,
            fencing_epoch=expected_bootstrap_fence,
            workspace_revision=_current_revision(),
            capabilities=capabilities or {},
        )

    async def fake_prepare_adaptation(workspace_id: UUID, **kwargs):
        adaptation_prepare_calls.append({"workspace_id": workspace_id, **kwargs})
        return project_cell_executor.RestorationAdaptationWorkspace(
            source_workspace_id=workspace_id,
            candidate_workspace_id=UUID(int=901),
            operation_id=kwargs["operation_id"],
            project_id=expected_project_id,
            owner_id=owner.id,
            generation_run_id=expected_run_id,
            candidate_fencing_epoch=1,
            source_database_digest="1" * 64,
            proof_digest="2" * 64,
            capabilities={
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
            },
        )

    async def fake_cleanup_adaptation(receipt):
        adaptation_cleanup_calls.append({"receipt": receipt})

    async def fake_prove_adaptation(receipt, **kwargs):
        adaptation_proof_calls.append({"receipt": receipt, **kwargs})
        return project_cell_executor.RestorationAdaptationProof(
            state="proof_ready",
            reason_code=None,
            source_workspace_id=receipt.source_workspace_id,
            candidate_workspace_id=receipt.candidate_workspace_id,
            operation_id=receipt.operation_id,
            project_id=receipt.project_id,
            owner_id=receipt.owner_id,
            generation_run_id=receipt.generation_run_id,
            candidate_fencing_epoch=receipt.candidate_fencing_epoch,
            proof_attempt=kwargs["proof_attempt"],
            source_workspace_revision=adaptation_prepare_calls[0]["source_workspace_revision"],
            candidate_workspace_revision=kwargs["candidate_workspace_revision"],
            candidate_proof_key=kwargs["candidate_proof_key"],
            candidate_artifact_digest=kwargs["candidate_artifact_digest"],
            source_database_digest=receipt.source_database_digest,
            candidate_database_digest=receipt.source_database_digest,
            source_schema_digest="3" * 64,
            candidate_schema_digest="3" * 64,
            source_business_digest="4" * 64,
            candidate_business_digest="4" * 64,
            source_technical_digest="5" * 64,
            candidate_technical_digest="5" * 64,
            probe_contract_digest="7" * 64,
            probe_rehearsal_digest="8" * 64,
            probe_rehearsal_database_digest="9" * 64,
            candidate_source_manifest_digest="a" * 64,
            proof_digest="6" * 64,
            capabilities={
                **receipt.capabilities,
                "restoration_adaptation_proof_v1": True,
            },
        )

    async def fake_identity(
        workspace_id: UUID,
        *,
        generation_run_id: UUID,
        fencing_epoch: int,
    ) -> ProjectCellWorkspaceIdentity:
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        if restoration_adaptation:
            assert workspace_id == UUID(int=901)
        return ProjectCellWorkspaceIdentity(
            workspace_revision=_current_revision(),
            dependency_digest="2" * 64,
            schema_data_digest="3" * 64,
            cell_manifest_digest="4" * 64,
            environment_digest="5" * 64,
            build_config_digest="6" * 64,
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
        write_workspaces.append(workspace_id)
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
        task_role: str | None = None,
        operation_id: UUID | None = None,
    ) -> ProjectCellAgentExecResponse:
        assert workspace_id
        assert generation_run_id == expected_run_id
        assert fencing_epoch == 1
        assert expected_revision == _current_revision()
        before_revision = _current_revision()
        exec_calls.append(
            {
                "cmd": cmd,
                **({"task_role": task_role, "operation_id": operation_id} if task_role else {}),
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
        identity_values = {
            "dependency_digest": "2" * 64,
            "schema_data_digest": "3" * 64,
            "cell_manifest_digest": "4" * 64,
            "environment_digest": "5" * 64,
            "build_config_digest": "6" * 64,
        }
        before_identity = ProjectCellWorkspaceIdentity(
            workspace_revision=before_revision,
            **identity_values,
        )
        after_identity = ProjectCellWorkspaceIdentity(
            workspace_revision=_current_revision(),
            **identity_values,
        )
        return ProjectCellAgentExecResponse(
            ok=bool(payload["ok"]),
            exit_code=int(payload["exit_code"]),
            detail=str(payload["detail"]),
            timed_out=bool(payload["timed_out"]),
            workspace_revision=_current_revision(),
            operation_id=operation_id,
            before_identity=before_identity if operation_id is not None else None,
            after_identity=after_identity if operation_id is not None else None,
            environment_mutated=before_identity != after_identity,
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
        return ProjectCellDraftApplyResponse.from_json(
            {
                **result,
                "state": draft_state,
                "workspace_revision": _current_revision(),
                "preview_url": "https://cell.preview.example.test",
            }
        )

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

    async def docs_media_execute(action: Action) -> dict[str, object]:
        legacy_actions.append(action.name)
        return {"ok": True, "detail": f"legacy:{action.name}"}

    if lifecycle_client is None:
        monkeypatch.setattr(
            project_cell_executor, "execute_cell_operation", fake_execute_cell_operation
        )
    else:
        monkeypatch.setattr(
            project_cell_executor, "HttpProjectCellOrchestratorClient", lambda: lifecycle_client
        )
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_agent_bootstrap",
        fake_bootstrap,
    )
    business_seed = AsyncMock(return_value=False)
    monkeypatch.setattr(project_cell_executor, "project_cell_apply_business_config", business_seed)
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_snapshot", fake_bootstrap)
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_prepare_restoration_adaptation",
        fake_prepare_adaptation,
    )
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_cleanup_restoration_adaptation",
        fake_cleanup_adaptation,
    )
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_prove_restoration_adaptation",
        fake_prove_adaptation,
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_identity", fake_identity)
    monkeypatch.setattr(
        project_cell_executor,
        "project_cell_agent_write_files",
        fake_write_files,
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", fake_exec)
    monkeypatch.setattr(project_cell_executor, "project_cell_apply_draft", fake_hot_reload)
    monkeypatch.setattr(project_cell_executor, "project_cell_create_preview_session", fake_preview)
    from yleum_api.services import orchestrator_client

    def forbidden_docs_media(*args, **kwargs):
        pytest.fail("selected cell must never call a legacy runtime")

    monkeypatch.setattr(orchestrator_client, "hot_reload", forbidden_docs_media)

    handle = await project_cell_executor.maybe_create_project_cell_executor(
        project_id=expected_project_id,
        project_slug=expected_project_slug,
        project_template="max_miniapp",
        user_id=owner.id,
        generation_run_id=expected_run_id,
        docs_media_execute=docs_media_execute,
        capacity_dispatch_token=capacity_dispatch_token,
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
        write_workspaces=write_workspaces,
        exec_calls=exec_calls,
        hot_reload_calls=hot_reload_calls,
        hot_reload_empty_files=hot_reload_empty_files,
        legacy_actions=legacy_actions,
        adaptation_prepare_calls=adaptation_prepare_calls,
        adaptation_proof_calls=adaptation_proof_calls,
        adaptation_cleanup_calls=adaptation_cleanup_calls,
    )


async def test_non_max_templates_skip_project_cell_executor() -> None:
    handle = await project_cell_executor.maybe_create_project_cell_executor(
        project_id=uuid4(),
        project_slug="plain-app",
        project_template="blank",
        user_id=uuid4(),
        generation_run_id=uuid4(),
        docs_media_execute=lambda _action: None,  # type: ignore[arg-type]
    )

    assert handle is None


async def test_immediate_ensure_with_dispatch_token_bootstraps_without_queueing(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    dispatch_token = uuid4()

    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        capacity_dispatch_token=dispatch_token,
    )

    db_session.expire_all()
    run = await db_session.get(GenerationRun, harness.run_id)
    operations = list(
        (
            await db_session.execute(
                select(ProjectCellOperation).where(
                    ProjectCellOperation.generation_run_id == harness.run_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert run is not None and run.status == "running"
    assert run.agent_state["capacity_admitted_dispatch_token"] == str(dispatch_token)
    assert [(operation.kind, operation.status) for operation in operations] == [
        ("ensure", "completed")
    ]


@pytest.mark.parametrize("old_ensure_status", ["completed", "indeterminate", "absent"])
@pytest.mark.parametrize("release_response_lost", [False, True])
async def test_same_project_recovers_terminal_binding_before_new_ensure(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    old_ensure_status: str,
    release_response_lost: bool,
) -> None:
    owner = await _new_user(db_session, "same-project")
    project = await _new_project(db_session, owner)
    old_run = await _new_run(db_session, project, owner, label="old", status="failed")
    new_run = await _new_run(db_session, project, owner, label="new")
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="provisioning",
        generation_run_id=old_run.id,
    )
    db_session.add(workspace)
    await db_session.flush()
    operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=old_run.id,
        kind="ensure",
        idempotency_key=f"old-ensure:{old_run.id}",
        request={"profile_version": "docker-owner-cell-resources-v1"},
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    await claim_cell_operation_committed(factory, operation.id)
    async with factory() as session:
        if old_ensure_status == "completed":
            await complete_cell_operation(session, operation.id, {})
        else:
            await mark_cell_operation_indeterminate(session, operation.id, "response lost")
        await session.commit()
    calls = []
    capacity_available = old_ensure_status != "absent"

    def response(request):
        return ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref="same-project",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    async def observe(request):
        calls.append("reconcile")
        if old_ensure_status == "absent":
            return replace(
                response(request),
                state="retained",
                has_workspace=False,
                has_agent_home=False,
                has_postgres=False,
                has_redis=False,
            )
        payload = response(request).to_wire_json()
        payload["lease_observation"] = {
            "version": 1,
            "workspace_id": str(workspace.id),
            "project_id": str(project.id),
            "owner_id": str(owner.id),
            "operation_id": str(request.operation_id),
            "request_digest": request.request_digest,
            "fencing_epoch": request.fencing_epoch,
            "active_generation_run_id": None,
            "active_generation_fencing_epoch": None,
            "quiescent": True,
        }
        return ProjectCellResourceResponse.from_json(payload)

    async def hibernate(_factory, *, requesting_run_id, client):
        nonlocal capacity_available
        assert requesting_run_id == new_run.id
        assert not capacity_available, "must not hibernate without capacity rejection"
        calls.append("hibernate")
        capacity_available = True
        return True

    async def control(request):
        assert request.kind == "release"
        async with factory() as session:
            bound = await session.get(ProjectCellWorkspace, workspace.id)
            assert bound.generation_run_id == old_run.id
        calls.append("release")
        if release_response_lost and calls.count("release") == 1:
            raise OrchestratorUnavailable("release response lost")
        return response(request)

    async def ensure(request):
        if request.generation_run_id == old_run.id:
            calls.append("repair")
            if not capacity_available:
                raise ProjectCellCapacityWait(
                    ProjectCellCapacityRejection(
                        operation_id=request.operation_id,
                        fencing_epoch=request.fencing_epoch,
                        request_digest=request.request_digest,
                        effect_applied=False,
                        reason="insufficient_cpu",
                        retry_after_seconds=1,
                    )
                )
            return response(request)
        assert request.generation_run_id == new_run.id
        assert calls[-1] == ("release" if old_ensure_status == "completed" else "reconcile")
        calls.append("ensure")
        return response(request)

    async def readiness(*_args, **_selection):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    async def bootstrap(_workspace_id, *, generation_run_id, fencing_epoch):
        assert generation_run_id == new_run.id
        assert fencing_epoch == {"completed": 3, "indeterminate": 3}[old_ensure_status]
        raise RuntimeError("test reached new bootstrap")

    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", readiness)
    monkeypatch.setattr(
        project_cell_executor,
        "HttpProjectCellOrchestratorClient",
        lambda: SimpleNamespace(ensure=ensure, control=control, observe_resources=observe),
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_bootstrap", bootstrap)
    monkeypatch.setattr(
        project_cell_capacity,
        "hibernate_one_idle_workspace",
        hibernate,
    )
    if old_ensure_status == "absent":
        with pytest.raises(
            project_cell_executor.ProjectCellExecutorUnavailable, match="needs reconciliation"
        ):
            await project_cell_executor.maybe_create_project_cell_executor(
                project_id=project.id,
                project_slug=project.slug,
                project_template="max_miniapp",
                user_id=owner.id,
                generation_run_id=new_run.id,
                docs_media_execute=ensure,
            )
        assert calls == ["reconcile"], "a terminal run must never acquire a fake repair lease"
        async with factory() as session:
            assert (
                await session.get(ProjectCellWorkspace, workspace.id)
            ).generation_run_id == old_run.id
        return
    with pytest.raises(RuntimeError, match="test reached new bootstrap"):
        await project_cell_executor.maybe_create_project_cell_executor(
            project_id=project.id,
            project_slug=project.slug,
            project_template="max_miniapp",
            user_id=owner.id,
            generation_run_id=new_run.id,
            docs_media_execute=ensure,
        )
    assert calls == (
        (
            ["reconcile"]
            if old_ensure_status == "indeterminate"
            else ["release"] * (2 if release_response_lost else 1)
        )
        + ["ensure"]
    )


async def test_concurrent_pending_dispatch_tokens_have_exactly_one_winner(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner = await _new_user(db_session, "pending-token-owner")
    project = await _new_project(db_session, owner, label="pending-token")
    run = await _new_run(db_session, project, owner, label="pending-token")
    run_id = run.id
    await db_session.commit()
    tokens = (uuid4(), uuid4())
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    results = await asyncio.gather(
        *(
            promote_generation_after_admission(
                factory,
                run_id=run_id,
                dispatch_token=token,
            )
            for token in tokens
        )
    )

    assert sorted(results) == ["admitted", "lost"]
    db_session.expire_all()
    persisted = await db_session.get(GenerationRun, run_id)
    assert persisted is not None and persisted.status == "running"
    winner = tokens[results.index("admitted")]
    assert persisted.agent_state["capacity_admitted_dispatch_token"] == str(winner)


async def test_adaptation_replaces_ordinary_project_database_before_agent_execution(
    monkeypatch,
    db_session,
    test_engine,
):
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        restoration_adaptation=True,
        snapshot_files={".omnia/cell.json": '{"version":1}'},
        capabilities={"portable_machine": True, "database_admin": "full"},
    )
    assert len(harness.adaptation_prepare_calls) == 1
    assert harness.handle.capabilities["database_admin"] == "isolated_copy"
    await harness.handle.release()


async def test_adaptation_accepts_controller_attested_isolated_copy(
    monkeypatch,
    db_session,
    test_engine,
):
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        restoration_adaptation=True,
        snapshot_files={".omnia/cell.json": '{"version":1}'},
        capabilities={
            "portable_machine": True,
            "database_admin": "isolated_copy",
            "restoration_adaptation_database_copy_v1": True,
        },
    )
    assert harness.handle.capabilities["database_admin"] == "isolated_copy"
    assert len(harness.adaptation_prepare_calls) == 1
    assert harness.legacy_actions == []
    assert harness.exec_calls == []
    await harness.handle.release()
    assert len(harness.adaptation_cleanup_calls) == 1


async def test_adaptation_exposes_candidate_identity_and_post_agent_proof(
    monkeypatch,
    db_session,
    test_engine,
):
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        restoration_adaptation=True,
        snapshot_files={".omnia/cell.json": '{"version":1}'},
        capabilities={"portable_machine": True, "database_admin": "full"},
    )

    candidate_workspace_id = UUID(int=901)
    assert harness.handle.workspace_id == candidate_workspace_id
    assert harness.handle.control_workspace_id == harness.workspace_id
    assert harness.handle.restoration_adaptation_workspace is not None
    assert harness.handle.prove_restoration_adaptation is not None

    identity = await harness.handle.current_identity()  # type: ignore[misc]
    assert identity.workspace_id == candidate_workspace_id
    proof = await harness.handle.prove_restoration_adaptation(identity, "a" * 64, 1)

    assert proof.state == "proof_ready"
    assert proof.candidate_workspace_id == candidate_workspace_id
    assert proof.candidate_proof_key == identity.proof_key
    assert harness.handle.capabilities["restoration_adaptation_proof_v1"] is True
    assert harness.adaptation_proof_calls == [
        {
            "receipt": harness.handle.restoration_adaptation_workspace,
            "candidate_workspace_revision": identity.workspace_revision,
            "candidate_proof_key": identity.proof_key,
            "candidate_artifact_digest": "a" * 64,
            "proof_attempt": 1,
        }
    ]
    await harness.handle.release()
    assert harness.adaptation_cleanup_calls == []


@pytest.mark.parametrize("adaptation", [False, True])
async def test_schema_alignment_build_is_allowed_only_in_controller_adaptation(
    monkeypatch, db_session, test_engine, adaptation,
):
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine,
        restoration_adaptation=adaptation,
        snapshot_files={
            ".omnia/cell.json": '{"version":1}',
            "src/lib/db/schema.ts": "subject: text('subject')",
        },
        capabilities={"portable_machine": True, "database_admin": "full"},
    )
    await harness.handle.execute(Action("write_file", {
        "path": "src/lib/db/schema.ts", "content": "subject: text('title')",
    }))
    try:
        result = await harness.handle.execute(Action("build", {}))
        assert result["ok"] is adaptation
        if adaptation:
            assert harness.exec_calls
            assert harness.handle.prove_restoration_adaptation is not None
        else:
            assert "MAX migration contract" in result["detail"]
            assert harness.exec_calls == []
    finally:
        await harness.handle.release()


async def test_failed_adaptation_candidate_diff_is_cleaned_without_source_write(
    monkeypatch,
    db_session,
    test_engine,
):
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        restoration_adaptation=True,
        snapshot_files={
            ".omnia/cell.json": '{"version":1}',
            "src/current.ts": "export const current = true;",
        },
        capabilities={"portable_machine": True, "database_admin": "full"},
    )

    result = await harness.handle.execute(
        Action(
            "write_file",
            {
                "path": "src/candidate-only.ts",
                "content": "export const candidate = true;",
            },
            "candidate edit before a failed or cancelled finalization",
        )
    )
    assert result["ok"] is True
    assert harness.write_workspaces == [UUID(int=901)]

    await harness.handle.release()

    assert harness.workspace_id not in harness.write_workspaces
    assert len(harness.adaptation_cleanup_calls) == 1


@pytest.mark.parametrize(
    "capabilities",
    [
        {},
        {"portable_machine": False, "database_admin": "full"},
        {"portable_machine": "true"},
    ],
)
async def test_adaptation_refuses_non_portable_bootstrap(
    monkeypatch,
    db_session,
    test_engine,
    capabilities,
):
    with pytest.raises(
        project_cell_executor.ProjectCellExecutorUnavailable,
        match="не подтверждена",
    ):
        await _prepare_executor(
            monkeypatch,
            db_session,
            test_engine,
            restoration_adaptation=True,
            snapshot_files={".omnia/cell.json": '{"version":1}'},
            capabilities=capabilities,
        )


async def test_adaptation_cannot_fall_back_to_legacy_execution(
    monkeypatch,
    db_session,
    test_engine,
):
    owner = await _new_user(db_session, "owner")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, project, owner)
    run.agent_state = {"restoration_adaptation": {"source_commit_sha": "a" * 40}}
    await db_session.commit()
    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)

    async def legacy_readiness(*_args, **_selection):
        return ProjectCellControlReadiness(
            selected=False,
            ready=False,
            provider="legacy",
            reason="not_selected",
        )

    async def forbidden_docs_media(_action):
        pytest.fail("adaptation must never obtain a legacy executor")

    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", legacy_readiness)
    with pytest.raises(project_cell_executor.ProjectCellExecutorUnavailable, match="Project Cell"):
        await project_cell_executor.maybe_create_project_cell_executor(
            project_id=project.id,
            project_slug=project.slug,
            project_template="max_miniapp",
            user_id=owner.id,
            generation_run_id=run.id,
            docs_media_execute=forbidden_docs_media,
        )


@pytest.mark.parametrize("modern", [False, True])
async def test_portable_executor_advertises_capabilities_and_dispatches_manifest_build(
    monkeypatch,
    db_session,
    test_engine,
    modern,
):
    roles = ["bootstrap", "fast_check", "full_build"] if modern else ["bootstrap", "build", "test"]
    manifest = {"version": 1, "tasks": [
        {"name": role, "role": role, "argv": ["python", "-c", "pass"]} for role in roles
    ]}
    monkeypatch.setattr(project_cell_executor, "get_settings", lambda: get_settings().model_copy(
        update={"use_max_finalization_coordinator": False},
    ))
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={".omnia/cell.json": json.dumps(manifest), "server.py": "print('python')"},
        capabilities={"portable_machine": True, "manifest_path": ".omnia/cell.json"},
    )
    original_exec = project_cell_executor.project_cell_agent_exec

    async def validate_role(*args, **kwargs):
        # Match the controller's production refusal: a modern manifest has no
        # legacy test role, so dispatching legacy build cannot succeed.
        if kwargs["task_role"] == "build" and "test" not in roles:
            raise ValueError("portable build requires a declared test task")
        assert kwargs["task_role"] in roles
        return await original_exec(*args, **kwargs)

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", validate_role)
    assert harness.handle.capabilities["portable_machine"] is True
    assert harness.handle.is_portable()
    result = await harness.handle.execute(Action(name="build", args={}))
    assert result["ok"]
    assert harness.exec_calls[0]["cmd"] == "omnia:build"
    assert harness.exec_calls[0]["task_role"] == ("full_build" if modern else "build")
    assert isinstance(harness.exec_calls[0]["operation_id"], UUID)


@pytest.mark.parametrize("with_receipt", [False, True])
async def test_full_build_executor_rejects_green_stdout_without_database_receipt(
    monkeypatch, db_session, test_engine, with_receipt,
):
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine,
        snapshot_files={".omnia/cell.json": '{"version":1}', "src/app/page.tsx": "product"},
        capabilities={"portable_machine": True},
        cell_exec_result={"ok": True, "exit_code": 0, "timed_out": False,
                          "detail": '[project-migrations:v1]{"contract":"project-migrations-v1"}'},
    )
    original_exec = project_cell_executor.project_cell_agent_exec

    async def response(workspace_id, *args, **kwargs):
        result = await original_exec(workspace_id, *args, **kwargs)
        receipt = {
            "contract": "project-migrations-v1", "mode": "apply",
            "workspace_id": str(workspace_id),
            "generation_run_id": str(kwargs["generation_run_id"]),
            "fencing_epoch": kwargs["fencing_epoch"],
            "source_revision": kwargs["expected_revision"],
            "source_digest": "2" * 64, "database_identity": "3" * 64,
            "catalog_digest": "4" * 64, "migration_count": 1,
        }
        return replace(result, project_migration_receipt=receipt if with_receipt else None)

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", response)
    result = await harness.handle.run_role(
        project_cell_executor.ProjectCellCommandRole.FULL_BUILD, uuid4(),
    )
    assert result.ok is with_receipt
    if not with_receipt:
        assert "migration receipt is missing or stale" in result.redacted_detail


@pytest.mark.parametrize("fault", [None, "migration", "compilation", "run", "source", "identity"])
async def test_restore_runtime_requires_bound_compilation_and_existing_database_receipts(
    monkeypatch,
    db_session,
    test_engine,
    fault,
):
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={".omnia/cell.json": '{"version":1}', "src/app/page.tsx": "accepted"},
        capabilities={"portable_machine": True},
        cell_exec_result={"ok": True, "exit_code": 0, "timed_out": False, "detail": "restored"},
    )
    original_exec = project_cell_executor.project_cell_agent_exec

    async def response(workspace_id, *args, **kwargs):
        assert kwargs["task_role"] == "restore_runtime"
        result = await original_exec(workspace_id, *args, **kwargs)
        migration = {
            "contract": "project-migrations-v1",
            "mode": "apply",
            "workspace_id": str(workspace_id),
            "generation_run_id": str(kwargs["generation_run_id"]),
            "fencing_epoch": kwargs["fencing_epoch"],
            "source_revision": kwargs["expected_revision"],
            "source_digest": "2" * 64,
            "database_identity": "3" * 64,
            "catalog_digest": "4" * 64,
            "migration_count": 1,
        }
        compilation = {
            "collector_version": "next-restored-runtime-v1",
            "workspace_id": str(workspace_id),
            "project_id": str(harness.project_id),
            "generation_run_id": str(kwargs["generation_run_id"]),
            "fencing_epoch": kwargs["fencing_epoch"],
            "operation_id": str(result.operation_id),
            "source_revision": kwargs["expected_revision"],
            "manifest_digest": result.before_identity.cell_manifest_digest,
        }
        if fault == "run":
            compilation["generation_run_id"] = str(uuid4())
        if fault == "source":
            compilation["source_revision"] = "f" * 64
        after = result.after_identity
        if fault == "identity":
            after = replace(after, workspace_revision="f" * 64)
        return replace(
            result,
            project_migration_receipt=None if fault == "migration" else migration,
            compiled_asset_receipt=None if fault == "compilation" else compilation,
            after_identity=after,
            workspace_revision=after.workspace_revision,
            environment_mutated=result.before_identity != after,
        )

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", response)
    result = await harness.handle.run_role(
        project_cell_executor.ProjectCellCommandRole.RESTORE_RUNTIME,
        uuid4(),
    )
    assert result.ok is (fault is None)


@pytest.mark.parametrize("ok,timed_out", [(True, False), (False, False), (False, True)])
async def test_clean_portable_shell_retains_preview_and_proof_identity(
    monkeypatch,
    db_session,
    test_engine,
    ok,
    timed_out,
):
    # Exercise draft-preview sync; coordinator mode intentionally skips draft apply.
    monkeypatch.setattr(project_cell_executor, "get_settings", lambda: get_settings().model_copy(
        update={"use_max_finalization_coordinator": False},
    ))
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={".omnia/cell.json": '{"version":1}', "src/app/page.tsx": "product"},
        capabilities={"portable_machine": True},
        cell_exec_result={
            "ok": ok,
            "exit_code": 0 if ok else 124,
            "timed_out": timed_out,
            "detail": "partial mutation",
        },
    )
    await harness.handle.sync_preview()
    assert len(harness.hot_reload_calls) == 1
    result = await harness.handle.execute(Action(name="bash", args={"cmd": "kill product"}))
    assert result["environment_mutated"] is False
    assert result["invalidated_dimensions"] == []
    assert not result.get("files")
    await harness.handle.sync_preview()
    assert len(harness.hot_reload_calls) == 1


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

    read = await harness.handle.execute(Action(name="read_file", args={"path": "src/app/page.tsx"}))
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
    added = await harness.handle.execute(Action(name="read_file", args={"path": "src/app/api.ts"}))
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


async def test_repeated_preview_checks_preserve_healthy_draft_until_source_changes(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    for _ in range(3):
        sync = await harness.handle.sync_preview()
        assert sync.failure is None
    assert harness.hot_reload_calls == [{}]
    await harness.handle.stage_patch({"src/app/page.tsx": "updated"})
    assert (await harness.handle.sync_preview()).failure is None
    assert harness.hot_reload_calls == [{}, {"src/app/page.tsx": "updated"}]


async def test_preview_checks_recover_stopped_draft_but_never_replay_auth_failure(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    from yleum_api.services.orchestrator_client import OrchestratorBadRequest

    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    await harness.handle.sync_preview()

    async def stopped(*args, **kwargs):
        raise OrchestratorBadRequest("draft runtime is not running", 409)

    monkeypatch.setattr(project_cell_executor, "project_cell_create_preview_session", stopped)
    assert (await harness.handle.sync_preview()).failure is None
    assert harness.hot_reload_calls == [{}, {}]

    async def released(*args, **kwargs):
        raise OrchestratorBadRequest("workspace generation lease is not active", 409)

    monkeypatch.setattr(project_cell_executor, "project_cell_create_preview_session", released)
    with pytest.raises(OrchestratorBadRequest, match="lease is not active"):
        await harness.handle.sync_preview()
    assert harness.hot_reload_calls == [{}, {}]


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
        "content_change": {
            "path": "empty.txt", "before_sha256": None,
            "after_sha256": hashlib.sha256(b"").hexdigest(),
        },
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
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        hot_reload_result={"pnpm_lockfile": "generated lock", "runtime_log_tail": "cell ready"},
    )
    result = await harness.handle.execute(Action(name="read_logs", args={}))
    assert result == {
        "ok": True,
        "detail": "cell ready",
        "files": {"pnpm-lock.yaml": "generated lock"},
    }
    assert harness.hot_reload_calls == [{}]
    assert harness.write_calls == []  # Generated files already belong to the cell volume.
    assert await harness.handle.snapshot_files() == {"pnpm-lock.yaml": "generated lock"}
    assert harness.legacy_actions == []


async def test_cell_rejects_removed_visual_tool_without_preview_or_legacy_io(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)

    result = await harness.handle.execute(Action(name="see", args={"path": "/"}))

    assert result == {"ok": False, "error": "unknown cell action see"}
    assert harness.hot_reload_calls == []
    assert harness.legacy_actions == []
    assert harness.write_calls == []


async def test_cell_migration_failure_cannot_be_reported_as_ready(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        hot_reload_result={"migration_exit_code": 1, "migration_stderr_tail": "schema conflict"},
    )
    with pytest.raises(
        project_cell_executor.ProjectCellExecutorUnavailable,
        match="schema conflict",
    ):
        await harness.handle.create_preview_session()
    assert harness.legacy_actions == []


async def test_cell_runtime_check_uses_workspace_session_not_legacy(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    from yleum_api.services import max_runtime_probe

    harness = await _prepare_executor(monkeypatch, db_session, test_engine)

    async def fake_probe(preview, *, path):
        assert preview.workspace_id == harness.workspace_id
        assert path == "/products"
        return max_runtime_probe.MaxRuntimeProbe(True, "cell database verified")

    monkeypatch.setattr(max_runtime_probe, "probe_max_cell_runtime", fake_probe)
    result = await harness.handle.execute(Action(name="runtime_check", args={"path": "/products"}))
    assert result == {"ok": True, "detail": "cell database verified"}
    assert harness.legacy_actions == []


async def test_portable_cell_runtime_check_falls_back_when_home_page_is_missing(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    from yleum_api.services import max_runtime_probe

    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={
            ".omnia/cell.json": '{"version":1}',
            "src/app/support/page.tsx": "export default function Support(){return null}\n",
        },
        capabilities={"portable_machine": True},
    )

    async def fake_probe(
        preview,
        *,
        path,
        fallback_paths=(),
        portable_project_id=None,
        expected_epoch=None,
    ):
        assert preview.workspace_id == harness.workspace_id
        assert path == "/"
        assert fallback_paths == ("/support",)
        assert portable_project_id == harness.project_id
        assert expected_epoch == 1
        return max_runtime_probe.MaxRuntimeProbe(True, "cell database verified via /support")

    monkeypatch.setattr(max_runtime_probe, "probe_max_cell_runtime", fake_probe)
    result = await harness.handle.execute(Action(name="runtime_check", args={"path": "/"}))
    assert result == {"ok": True, "detail": "cell database verified via /support"}
    assert harness.legacy_actions == []


async def test_ready_mark_cannot_steal_a_newer_workspace_lease(
    monkeypatch,
    db_session,
    test_engine,
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
        project_cell_executor.ProjectCellExecutorUnavailable,
        match="lease changed",
    ):
        await project_cell_executor._mark_workspace_ready(
            session_factory=factory,
            workspace_id=harness.workspace_id,
            generation_run_id=harness.run_id,
            provider_ref="stale",
            fencing_epoch=1,
        )
    db_session.expire_all()
    preserved = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert preserved is not None
    assert preserved.generation_run_id == newer_run_id
    assert preserved.fencing_epoch == 2
    assert preserved.provider_ref == "cell-1"


async def test_ready_mark_reloads_cancellation_from_another_session(
    monkeypatch,
    db_session,
    test_engine,
) -> None:
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    workspace = await db_session.get(ProjectCellWorkspace, harness.workspace_id)
    assert workspace is not None
    workspace.state = "provisioning"
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as cancel_session:
        run = await cancel_session.scalar(
            select(GenerationRun).where(GenerationRun.id == harness.run_id).with_for_update()
        )
        assert run is not None
        run.status = "cancel_requested"
        activation = asyncio.create_task(
            project_cell_executor._mark_workspace_ready(
                session_factory=factory,
                workspace_id=harness.workspace_id,
                generation_run_id=harness.run_id,
                provider_ref="stale",
                fencing_epoch=1,
            )
        )
        await asyncio.sleep(0.2)
        assert not activation.done(), "activation did not wait for the run-row fence"
        await cancel_session.commit()
        with pytest.raises(
            project_cell_executor.ProjectCellExecutorUnavailable,
            match="lease changed",
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

    result = await harness.handle.execute(Action(name="bash", args={"cmd": "pnpm test"}))

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


@pytest.mark.parametrize("existing_cell", [False, True])
async def test_disabled_routing_never_falls_back_for_a_durable_cell(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    existing_cell: bool,
) -> None:
    owner = await _new_user(db_session, "disabled-owner")
    project = await _new_project(db_session, owner, label="disabled")
    run = await _new_run(db_session, project, owner, label="disabled")
    if existing_cell:
        db_session.add(
            ProjectCellWorkspace(
                project_id=project.id,
                owner_id=owner.id,
                provider="docker_owner_canary",
                state="ready",
                generation_run_id=run.id,
                fencing_epoch=1,
            )
        )
    await db_session.commit()
    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)

    async def disabled_readiness(_user, _project_id, **_selection):
        return ProjectCellControlReadiness(
            selected=False,
            ready=False,
            provider="legacy",
            reason="feature_disabled",
        )

    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", disabled_readiness)

    async def forbidden_docs_media(_action):
        pytest.fail("must not execute legacy commands")

    kwargs = dict(
        project_id=project.id,
        project_slug=project.slug,
        project_template="max_miniapp",
        user_id=owner.id,
        generation_run_id=run.id,
        docs_media_execute=forbidden_docs_media,
    )
    if existing_cell:
        with pytest.raises(
            project_cell_executor.ProjectCellExecutorUnavailable,
            match="legacy execution is disabled",
        ):
            await project_cell_executor.maybe_create_project_cell_executor(**kwargs)
    else:
        assert await project_cell_executor.maybe_create_project_cell_executor(**kwargs) is None


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

    async def unready_readiness(_user, _project_id, **_selection):
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
            docs_media_execute=lambda _action: None,  # type: ignore[arg-type]
        )

    assert "Project Cell selected but not ready" in str(caught.value)


@pytest.mark.parametrize("mismatch", ["run_id", "fencing_epoch"])
async def test_bootstrap_rejects_mismatched_active_lease_before_agent_activation(
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

    async def ready_readiness(_user, _project_id, **_selection):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    async def fake_execute_cell_operation(_session_factory, operation_id, _client):
        workspace = await db_session.scalar(
            select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run.id)
        )
        assert workspace is not None
        workspace.fencing_epoch = 1
        await db_session.commit()
        return SimpleNamespace(
            operation_id=operation_id,
            kind="ensure",
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
            docs_media_execute=lambda _action: None,  # type: ignore[arg-type]
        )

    assert "active lease does not match the run" in str(caught.value)
    db_session.expire_all()
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run_id)
    )
    assert workspace is not None
    # Ensure proved physical resources ready, but a mismatched bootstrap must
    # still raise instead of returning an executor with agent write authority.
    assert workspace.state == "ready"


async def test_bootstrap_wraps_orchestrator_bad_request_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner = await _new_user(db_session, "bootstrap-bad-request-owner")
    project = await _new_project(db_session, owner, label="bootstrap-bad-request")
    run = await _new_run(db_session, project, owner, label="bootstrap-bad-request")
    await db_session.commit()

    async def ready_readiness(_user, _project_id, **_selection):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    async def fake_execute_cell_operation(_session_factory, operation_id, _client):
        workspace = await db_session.scalar(
            select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run.id)
        )
        assert workspace is not None
        workspace.fencing_epoch = 1
        await db_session.commit()
        return SimpleNamespace(
            operation_id=operation_id,
            kind="ensure",
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
            docs_media_execute=lambda _action: None,  # type: ignore[arg-type]
        )

    assert "workspace generation lease mismatch" in str(caught.value)


async def test_queued_cancel_survives_outer_flow_finalize_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    owner = await _new_user(db_session, "cancel-admission-owner")
    project = await _new_project(db_session, owner, label="cancel-admission")
    assistant = Message(project_id=project.id, role="assistant", content="")
    db_session.add(assistant)
    await db_session.flush()
    run = await _new_run(
        db_session,
        project,
        owner,
        label="cancel-admission",
        status="queued_for_capacity",
    )
    run.assistant_message_id = assistant.id
    dispatch_token = uuid4()
    write_capacity_dispatch_claim(run, token=dispatch_token, lease_seconds=30)
    await db_session.commit()
    ensure_running = asyncio.Event()
    cancellation_committed = asyncio.Event()
    cancellation_signal = asyncio.Event()
    executed_kinds: list[str] = []
    signalled_runs: list[UUID] = []

    async def ready_readiness(_user, _project_id, **_selection):
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )

    async def fake_execute_cell_operation(session_factory, operation_id, _client):
        claimed = await claim_cell_operation_committed(session_factory, operation_id)
        executed_kinds.append(claimed.kind)
        if claimed.kind == "ensure":
            ensure_running.set()
            await cancellation_committed.wait()
        response = ProjectCellResourceResponse(
            workspace_id=claimed.workspace_id,
            state="resources_ready",
            provider_ref="cell-cancel-admission",
            fencing_epoch=claimed.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )
        async with session_factory() as session:
            await complete_cell_operation(session, operation_id, response.to_wire_json())
            await session.commit()
        return SimpleNamespace(
            operation_id=operation_id,
            kind=claimed.kind,
            status="completed",
            response=response,
        )

    async def forbidden_bootstrap(*_args, **_kwargs):
        pytest.fail("cancelled admission must not start generation bootstrap")

    async def noop(*_args, **_kwargs):
        return None

    async def signal_cancel(run_id: UUID) -> None:
        signalled_runs.append(run_id)
        cancellation_signal.set()

    async def wait_for_cancel(_run_id: UUID) -> None:
        await cancellation_signal.wait()

    async def never_lose_lease(_run_id: UUID, _token: UUID) -> str:
        await asyncio.Future()
        return "lost"

    async def outer_project_cell_flow() -> None:
        try:
            await project_cell_executor.maybe_create_project_cell_executor(
                project_id=project.id,
                project_slug=project.slug,
                project_template="max_miniapp",
                user_id=owner.id,
                generation_run_id=run.id,
                docs_media_execute=lambda _action: None,  # type: ignore[arg-type]
                capacity_dispatch_token=dispatch_token,
            )
        except project_cell_executor.ProjectCellExecutorUnavailable:
            # The outer build flow converts Project Cell setup failure into a
            # normal product outcome, after the fenced release has completed.
            return

    async def finalize_with_test_database(run_id: UUID) -> str:
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            return await finalize_generation_run(run_id, session)

    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", ready_readiness)
    monkeypatch.setattr(
        project_cell_executor,
        "execute_cell_operation",
        fake_execute_cell_operation,
    )
    monkeypatch.setattr(project_cell_executor, "project_cell_agent_bootstrap", forbidden_bootstrap)
    monkeypatch.setattr(messages, "request_generation_cancel", signal_cancel)
    monkeypatch.setattr(supervisor, "publish_event", noop)
    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_for_cancel)
    monkeypatch.setattr(supervisor, "_wait_for_capacity_dispatch_lease_loss", never_lose_lease)
    monkeypatch.setattr(supervisor, "_finalize_cancelled_generation", noop)
    monkeypatch.setattr(supervisor, "set_generation_run_status", noop)
    monkeypatch.setattr(supervisor, "_emergency_error", noop)
    monkeypatch.setattr(supervisor, "clear_generation_cancel", noop)
    monkeypatch.setattr(supervisor, "finalize_generation_run", finalize_with_test_database)

    task = asyncio.create_task(
        supervisor._run_tracked_prompt(
            outer_project_cell_flow(),
            run_id=run.id,
            project_id=project.id,
            assistant_message_id=assistant.id,
            label="cancel-during-admission",
            capacity_dispatch_token=dispatch_token,
        )
    )
    await ensure_running.wait()
    async with AsyncSession(test_engine, expire_on_commit=False) as cancel_session:
        cancelled = await messages.cancel_active_generation(
            project.id,
            cancel_session,
            owner,
        )
        assert cancelled.status == "cancelled"
    cancellation_committed.set()
    await task

    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        persisted_run = await session.get(GenerationRun, run.id)
        workspace = await session.scalar(
            select(ProjectCellWorkspace).where(ProjectCellWorkspace.project_id == project.id)
        )
        operations = list(
            (
                await session.execute(
                    select(ProjectCellOperation)
                    .where(ProjectCellOperation.workspace_id == workspace.id)
                    .order_by(ProjectCellOperation.fencing_epoch)
                )
            )
            .scalars()
            .all()
        )
    assert persisted_run is not None and persisted_run.status == "cancelled"
    assert persisted_run.error is None
    assert workspace is not None and workspace.generation_run_id is None
    assert signalled_runs == []
    assert executed_kinds == ["ensure", "release"]
    assert [(item.kind, item.status) for item in operations] == [
        ("ensure", "completed"),
        ("release", "completed"),
    ]


async def test_executor_does_not_turn_protected_environment_failure_into_model_observation(
    monkeypatch,
    db_session,
    test_engine,
):
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={".omnia/cell.json": '{"version":1}', "src/app/page.tsx": "product"},
        capabilities={"portable_machine": True},
    )

    async def fail_exec(*args, **kwargs):
        raise OrchestratorBadRequest(
            "private diagnostic",
            status_code=409,
            upstream_code="protected_environment_recovery_required",
        )

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", fail_exec)
    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await harness.handle.execute(Action(name="build", args={}))


async def test_saved_role_response_replays_without_command_and_rejects_other_identity(
    monkeypatch,
    db_session,
    test_engine,
):
    from dataclasses import replace

    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={".omnia/cell.json": '{"version":1}', "src/app/page.tsx": "product"},
        capabilities={"portable_machine": True},
    )
    original_exec = project_cell_executor.project_cell_agent_exec
    saved = []

    async def capture(*args, **kwargs):
        result = await original_exec(*args, **kwargs)
        saved.append(result)
        return result

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", capture)
    role = project_cell_executor.ProjectCellCommandRole.FULL_BUILD
    operation_id = uuid4()
    original = await harness.handle.run_role(role, operation_id)

    async def forbidden(*args, **kwargs):
        pytest.fail("terminal replay sent another command")

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", forbidden)
    replay = await harness.handle.replay_role_response(role, saved[0], original.before)
    assert replay == original and len(harness.exec_calls) == 1
    with pytest.raises(
        project_cell_executor.ProjectCellExecutorUnavailable, match="proof identity mismatch"
    ):
        await harness.handle.replay_role_response(
            role, saved[0], replace(original.before, fencing_epoch=2)
        )
    assert len(harness.exec_calls) == 1


@pytest.mark.parametrize("action", [
    Action(name="write_file", args={"path": "src/a.ts", "content": "changed"}),
    Action(name="bash", args={"cmd": "echo synthetic"}),
    Action(name="build", args={}),
])
async def test_v2_executor_without_owned_effect_never_dispatches(
    monkeypatch, db_session, test_engine, action,
):
    from tests.test_generation_recovery_execution import _bootstrap_wait
    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.run_id)
    run.prompt_hash = "a" * 64
    run.agent_state = {"recovery_v1": _bootstrap_wait(run)}
    await db_session.commit()
    before = (len(harness.write_calls), len(harness.exec_calls))
    result = await harness.handle.execute(action)
    assert result["ok"] is False
    assert (len(harness.write_calls), len(harness.exec_calls)) == before


async def test_v2_release_records_owned_terminal_operation_before_continuation(
    monkeypatch,
    db_session,
    test_engine,
):
    from tests.test_generation_recovery_execution import _bootstrap_wait
    from yleum_api.services.generation.recovery_execution import recovery_state

    harness = await _prepare_executor(monkeypatch, db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.run_id)
    run.prompt_hash = "a" * 64
    state = _bootstrap_wait(run)
    state["execution"]["physical_state"] = "active"
    run.agent_state = {"recovery_v1": state}
    await db_session.commit()
    await harness.handle.release()
    await db_session.refresh(run)
    current = recovery_state(run)
    assert current.execution.physical_state == "released"
    assert current.execution.release_operation_id is not None
    operation = await db_session.get(ProjectCellOperation, current.execution.release_operation_id)
    assert operation.kind == "release" and operation.status == "completed"
    assert operation.generation_run_id == run.id
    assert operation.idempotency_key.endswith(":cycle:0")
    assert current.execution.control_workspace_id == harness.handle.control_workspace_id
    assert current.execution.control_fencing_epoch == operation.fencing_epoch


@pytest.mark.parametrize("action_name", ["write_file", "edit_file", "stage_patch"])
@pytest.mark.parametrize(
    "damage",
    [
        None,
        "pending",
        "foreign",
        "request",
        "post_state",
        "cancel",
        "context",
        "partial",
        "incarnation_during_read",
        "noop",
        "driver",
        "bootstrap",
    ],
)
async def test_v2_write_ack_loss_reconciles_receipt_before_checkpoint_without_replay(
    monkeypatch,
    db_session,
    test_engine,
    action_name,
    damage,
):
    install_healthy_reader_inventory(monkeypatch, refresh=True)
    from datetime import timedelta

    from tests.test_generation_recovery_execution import _bootstrap_wait
    from yleum_api.services import orchestrator_client
    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation import source_checkpoint
    from yleum_api.services.generation.recovery_contract import ContinuationClaim
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        tool_effect_scope,
    )

    objects = {}

    async def store(project_id, run_id, data):
        sha = hashlib.sha256(data).hexdigest()
        key = f"{project_id}/{run_id}/{sha}"
        objects[key] = data
        return key, sha

    async def load(project_id, run_id, *, object_key, digest):
        if object_key != f"{project_id}/{run_id}/{digest}":
            return None
        data = objects.get(object_key)
        return data if data is not None and hashlib.sha256(data).hexdigest() == digest else None

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", load)
    before = {"src/app/page.tsx": "old", "src/remove.ts": "obsolete"}
    harness = await _prepare_executor(monkeypatch, db_session, test_engine, snapshot_files=before)
    run = await db_session.get(GenerationRun, harness.run_id)
    run.status, run.prompt_hash = "running", "a" * 64
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=1,
        worker_id="synthetic",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    cursor = ToolEffectCursor(claim, 0, 0, "write-once")
    state = _bootstrap_wait(run)
    state.update(
        phase="working",
        next_check_at=None,
        attempt_seq=1,
        continuation_claim=claim.model_dump(mode="json"),
    )
    state["execution"].update(physical_state="active", pending_effect_id=str(cursor.root_effect_id))
    run.agent_state = {"recovery_v1": state}
    await db_session.commit()
    original = project_cell_executor.project_cell_agent_write_files
    receipts = {}
    status_calls = 0
    target_content = "old" if damage == "noop" else "paid change"

    async def lose_ack(workspace_id, *, operation_id, **kwargs):
        if damage == "partial":
            await original(workspace_id, **{**kwargs, "files": {}})
            raise OrchestratorUnavailable("synthetic partial preterminal write")
        response = await original(workspace_id, **kwargs)
        if damage == "noop":
            response = replace(response, written=0)
        request = {
            "protocol": "source-write/v1",
            "workspace_id": str(workspace_id),
            "generation_run_id": str(run.id),
            "fencing_epoch": kwargs["fencing_epoch"],
            "expected_revision": kwargs["expected_revision"],
            "files": {
                p: hashlib.sha256(c.encode()).hexdigest() for p, c in kwargs["files"].items()
            },
            "deletes": sorted(kwargs.get("deletes", ())),
        }
        receipts[operation_id] = orchestrator_client.ProjectCellSourceWriteReceipt(
            version=1,
            operation_id=operation_id,
            workspace_id=workspace_id,
            project_id=run.project_id,
            owner_id=run.user_id,
            generation_run_id=run.id,
            fencing_epoch=kwargs["fencing_epoch"],
            physical_volume="synthetic-owned-volume",
            physical_volume_identity="d" * 64,
            request_sha256=hashlib.sha256(
                json.dumps(
                    request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            before_revision=kwargs["expected_revision"],
            after_revision=response.workspace_revision,
            written=response.written,
            deleted=response.deleted,
        )
        raise OrchestratorUnavailable("synthetic post-terminal acknowledgement lost")

    async def status(workspace_id, *, operation_id, generation_run_id):
        nonlocal status_calls
        status_calls += 1
        assert workspace_id == harness.workspace_id and generation_run_id == run.id
        if damage in {"pending", "partial"}:
            return orchestrator_client.ProjectCellSourceWriteStatus(operation_id, "pending", None)
        receipt = receipts[operation_id]
        if damage == "incarnation_during_read" and status_calls > 1:
            receipt = replace(receipt, physical_volume_identity="f" * 64)
        if damage == "foreign":
            receipt = replace(receipt, owner_id=uuid4())
        elif damage == "request":
            receipt = replace(receipt, request_sha256="e" * 64)
        elif damage == "post_state":
            receipt = replace(receipt, after_revision="f" * 64)
        elif damage == "cancel":
            run.status = "cancel_requested"
            await db_session.commit()
        return orchestrator_client.ProjectCellSourceWriteStatus(operation_id, "terminal", receipt)

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_write_files", lose_ack)
    monkeypatch.setattr(orchestrator_client, "project_cell_agent_write_operation", status)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = ex._SESSION_OWNER.set(ex.SessionOwnership(run.id, owner))
        try:
            args = {"path": "src/app/page.tsx"}
            args.update(
                {"content": target_content}
                if action_name == "write_file"
                else {"search": "old", "replace": target_content}
            )
            action = Action(action_name, args, "")

            async def execute():
                if action_name != "stage_patch":
                    return await harness.handle.execute(action)
                try:
                    await harness.handle.stage_patch(
                        {"src/app/page.tsx": target_content}, ("src/remove.ts",)
                    )
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}
                return {"ok": True}

            if damage in {"driver", "bootstrap"}:
                from copy import deepcopy

                from sqlalchemy.ext.asyncio import async_sessionmaker

                from yleum_api.services.generation.recovery_contract import (
                    CheckpointRef,
                    RecoveryBinding,
                )

                factory = async_sessionmaker(test_engine, expire_on_commit=False)
                initial = ex.recovery_state(run)
                ref = CheckpointRef(object_key="synthetic/pre-tool", sha256="b" * 64)
                initial = initial.model_copy(
                    update={
                        "cursor": "bootstrap" if damage == "bootstrap" else "agent",
                        "checkpoint": ref,
                        "context_ref": ref,
                        "binding": RecoveryBinding(
                            workspace_id=str(harness.workspace_id),
                            fencing_epoch=1,
                            source_sha256="a" * 64,
                            schema_sha256="b" * 64,
                            environment_sha256="c" * 64,
                        ),
                        "execution": initial.execution.model_copy(
                            update={
                                "stage": "bootstrap" if damage == "bootstrap" else "agent",
                                "substep": (
                                    "runtime_prepare" if damage == "bootstrap"
                                    else "response_validated"
                                ),
                                "provider_effect_id": None if damage == "bootstrap" else uuid4(),
                                "execution_context_ref": ref,
                            }
                        ),
                    }
                )
                run.agent_state = {"recovery_v1": initial.model_dump(mode="json")}
                await db_session.commit()

                async def commit(factory, *, previous, execution, context, phase="working"):
                    updated = previous.model_copy(
                        update={
                            "execution": execution,
                            "phase": phase,
                            "next_check_at": datetime.now(UTC) if phase != "working" else None,
                            "state_revision": previous.state_revision + 1,
                        }
                    )
                    async with factory() as session:
                        current = await session.get(GenerationRun, run.id, with_for_update=True)
                        assert ex.recovery_state(current) == previous
                        current.agent_state = {"recovery_v1": updated.model_dump(mode="json")}
                        await session.commit()
                    return updated

                monkeypatch.setattr(ex, "commit_execution_cursor", commit)
                snapshots = []

                def new_driver(current, context):
                    driver = ex.ExecutionDriver(factory, current, context, None)

                    async def save(execution, *, context, phase="working"):
                        # Falsifier: no source checkpoint until terminal receipt and
                        # fresh post-state/second incarnation check both completed.
                        if damage != "bootstrap" or execution.pending_effect_id is None:
                            assert status_calls >= 2
                            snapshots.append(dict(await harness.handle.snapshot_files()))
                        driver.state = await commit(
                            factory,
                            previous=driver.state,
                            execution=execution,
                            context=context,
                            phase=phase,
                        )
                        driver.context = context

                    monkeypatch.setattr(driver, "save", save)
                    return driver

                if damage == "bootstrap":
                    from unittest.mock import AsyncMock

                    from yleum_api.services.generation.recovery_pipeline import run_bootstrap_stage

                    async def prepare():
                        current_files = await harness.handle.snapshot_files()
                        if current_files.get("src/app/page.tsx") != target_content:
                            await harness.handle.stage_patch(
                                {"src/app/page.tsx": target_content},
                                ("src/remove.ts",) if action_name == "stage_patch" else (),
                            )
                        return await harness.handle.snapshot_files(), None

                    driver = new_driver(initial, {"completed": {}})
                    with pytest.raises(ex.GenerationYielded):
                        await run_bootstrap_stage(
                            driver, "starter_apply", prepare, handle=harness.handle,
                        )
                    assert len(harness.write_calls) == 1 and snapshots == []
                    async with factory() as session:
                        current = await session.get(GenerationRun, run.id)
                        resumed = ex.recovery_state(current).model_copy(
                            update={"phase": "working", "next_check_at": None}
                        )
                        current.agent_state = {"recovery_v1": resumed.model_dump(mode="json")}
                        await session.commit()
                    cold = new_driver(resumed, deepcopy(driver.context))
                    result, agent_result = await run_bootstrap_stage(
                        cold, "starter_apply", prepare, handle=harness.handle,
                    )
                    assert result["src/app/page.tsx"] == target_content and agent_result is None
                    assert len(harness.write_calls) == 1 and len(snapshots) == 1
                    # Receipt checked even though conditional preparation skipped the write.
                    assert status_calls >= 2
                    assert await run_bootstrap_stage(
                        cold, "starter_apply",
                        AsyncMock(side_effect=AssertionError("replayed stage")),
                        handle=harness.handle,
                    ) == (result, None)
                    return
                driver = new_driver(initial, {"schema_version": 1, "entry": "pipeline"})
                with pytest.raises(ex.GenerationYielded):
                    await driver.tool("write-once", action, execute)
                assert len(harness.write_calls) == 1 and snapshots == []
                async with factory() as session:
                    current = await session.get(GenerationRun, run.id)
                    resumed = ex.recovery_state(current).model_copy(
                        update={"phase": "working", "next_check_at": None}
                    )
                    current.agent_state = {"recovery_v1": resumed.model_dump(mode="json")}
                    await session.commit()
                cold = new_driver(resumed, deepcopy(driver.context))
                result = await cold.tool("write-once", action, execute)
                assert result["ok"] is True and len(harness.write_calls) == 1
                expected = {"src/app/page.tsx": target_content}
                if action_name != "stage_patch":
                    expected["src/remove.ts"] = "obsolete"
                    assert (
                        result["content_change"]["before_sha256"]
                        == hashlib.sha256(b"old").hexdigest()
                    )
                assert snapshots == [expected]
                return
            with tool_effect_scope(cursor):
                first = await execute()
            assert first["ok"] is False and len(harness.write_calls) == 1
            expected = {"src/app/page.tsx": "old" if damage == "partial" else target_content}
            if action_name != "stage_patch":
                expected["src/remove.ts"] = "obsolete"
            assert await harness.handle.refresh_snapshot_files() == expected
            if damage == "context":
                objects.clear()  # Missing private executable data is never source authority.
            cold = ToolEffectCursor(claim, 0, 0, "write-once")
            with tool_effect_scope(cold):
                result = await execute()
            assert result["ok"] is (damage in {None, "noop"}), result
            if damage in {None, "noop"} and action_name != "stage_patch":
                assert (
                    result["content_change"]["before_sha256"] == hashlib.sha256(b"old").hexdigest()
                )
                assert (
                    result["content_change"]["after_sha256"]
                    == hashlib.sha256(target_content.encode()).hexdigest()
                )
            assert len(harness.write_calls) == 1
            assert await harness.handle.snapshot_files() == expected
            if damage is None:
                operation_id = next(iter(receipts))
                receipts[operation_id] = replace(
                    receipts[operation_id], physical_volume_identity="e" * 64
                )
                with tool_effect_scope(ToolEffectCursor(claim, 0, 0, "write-once")):
                    changed_instance = await execute()
                assert changed_instance["ok"] is False
                assert len(harness.write_calls) == 1
        finally:
            ex._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("damage", [
    None, "pending", "foreign", "request", "incarnation", "cancel",
    "driver", "role", "coordinator", "coordinator_tool",
    "role_foreign", "role_proof", "role_cancel", "role_expired", "role_cold_proof"])
async def test_v2_command_ack_loss_reads_terminal_receipt_without_dispatch(
    monkeypatch,
    db_session,
    test_engine,
    damage,
):
    install_healthy_reader_inventory(monkeypatch, refresh=True)
    from datetime import timedelta

    from tests.test_generation_recovery_execution import _bootstrap_wait
    from yleum_api.services import orchestrator_client as client
    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation import source_checkpoint
    from yleum_api.services.generation.recovery_contract import ContinuationClaim
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        tool_effect_scope,
    )

    objects = {}

    async def store(project_id, run_id, data):
        sha = hashlib.sha256(data).hexdigest()
        key = f"{project_id}/{run_id}/{sha}"
        objects[key] = data
        return key, sha

    async def load(project_id, run_id, *, object_key, digest):
        data = objects.get(object_key)
        return data if data is not None and hashlib.sha256(data).hexdigest() == digest else None

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", load)
    command_changes = {"src/a.ts": "completed command"}
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files={".omnia/cell.json": '{"version":1}', "src/a.ts": "old"},
        cell_exec_files=command_changes,
        capabilities={"portable_machine": True},
    )
    run = await db_session.get(GenerationRun, harness.run_id)
    run.status, run.prompt_hash = "running", "a" * 64
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=1,
        worker_id="synthetic",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    cursor = ToolEffectCursor(claim, 0, 0, "command-once")
    raw = _bootstrap_wait(run)
    raw.update(
        phase="working",
        next_check_at=None,
        attempt_seq=1,
        continuation_claim=claim.model_dump(mode="json"),
    )
    raw["execution"].update(physical_state="active", pending_effect_id=str(cursor.root_effect_id))
    run.agent_state = {"recovery_v1": raw}
    await db_session.commit()
    is_role = isinstance(damage, str) and damage.startswith("role")
    role_operation = uuid4()
    if is_role:
        from yleum_api.models.project_cell import ProjectCellActivityLease
        physical = await harness.handle.current_identity()
        now = datetime.now(UTC)
        db_session.add(ProjectCellActivityLease(
            operation_id=role_operation, workspace_id=harness.workspace_id,
            generation_run_id=run.id, kind="command", state="active", fencing_epoch=1,
            phase="fast_check", proof_key=physical.proof_key, started_at=now, heartbeat_at=now,
            deadline_at=now+timedelta(minutes=5),
        ))
        await db_session.commit()
    original = project_cell_executor.project_cell_agent_exec
    saved, sent, reads = {}, [], []

    async def lose_ack(workspace, cmd, **kw):
        sent.append(dict(kw))
        track = kw.pop("track_receipt", False)
        response = await original(workspace, cmd, **kw)
        request = {**kw, "cmd": cmd, "task_role": kw.get("task_role"), "track_receipt": True}
        request["generation_run_id"] = str(run.id)
        request["operation_id"] = str(kw["operation_id"])
        receipt = client.ProjectCellCommandReceipt(
            version=1,
            operation_id=kw["operation_id"],
            workspace_id=workspace,
            project_id=run.project_id,
            owner_id=run.user_id,
            generation_run_id=run.id,
            dispatch_fencing_epoch=kw["fencing_epoch"],
            physical_volume="owned-command-volume",
            physical_volume_identity="d" * 64,
            before_revision=kw["expected_revision"],
            after_revision=response.workspace_revision,
            request_sha256=client.command_wire_digest(
                dict(protocol="tracked-command/v1", workspace_id=str(workspace), request=request)
            ),
            response_sha256=client.command_wire_digest(response.command_wire_json()),
        )
        saved.update(response=response, receipt=receipt, tracked=track)
        if saved.get("acknowledge"):
            return replace(response, command_receipt=receipt)
        raise OrchestratorUnavailable("synthetic terminal command ACK lost")

    async def status(workspace, *, operation_id, generation_run_id, fencing_epoch):
        reads.append(operation_id)
        assert workspace == harness.workspace_id and generation_run_id == run.id
        assert fencing_epoch == 1
        if damage == "pending":
            return client.ProjectCellCommandStatus(operation_id, "pending", None, None)
        receipt = saved["receipt"]
        if damage == "foreign":
            receipt = replace(receipt, owner_id=uuid4())
        elif damage == "request":
            receipt = replace(receipt, request_sha256="9" * 64)
        elif damage == "incarnation" and len(reads) > 1:
            receipt = replace(receipt, physical_volume_identity="9" * 64)
        elif damage == "cancel":
            run.status = "cancel_requested"
            await db_session.commit()
        return client.ProjectCellCommandStatus(operation_id, "terminal", receipt, saved["response"])

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", lose_ack)
    monkeypatch.setattr(client, "project_cell_agent_command_operation", status)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = ex._SESSION_OWNER.set(ex.SessionOwnership(run.id, owner))
        try:
            action = Action("bash", {"cmd": "synthetic-write"}, "")
            if damage in {"driver", "coordinator", "coordinator_tool"}:
                from copy import deepcopy

                from sqlalchemy.ext.asyncio import async_sessionmaker

                from yleum_api.services.generation.recovery_contract import (
                    CheckpointRef,
                    RecoveryBinding,
                )

                factory = async_sessionmaker(test_engine, expire_on_commit=False)
                initial = ex.recovery_state(run)
                ref = CheckpointRef(object_key="synthetic/pre-tool", sha256="b" * 64)
                initial = initial.model_copy(
                    update={
                        "cursor": "finalization" if damage == "coordinator" else "agent",
                        "checkpoint": ref,
                        "context_ref": ref,
                        "binding": RecoveryBinding(
                            workspace_id=str(harness.workspace_id),
                            fencing_epoch=1,
                            source_sha256="a" * 64,
                            schema_sha256="b" * 64,
                            environment_sha256="c" * 64,
                        ),
                        "execution": initial.execution.model_copy(
                            update={
                                "stage": "finalization" if damage == "coordinator" else "agent",
                                "substep": ("checkpoint_ready" if damage == "coordinator"
                                            else "response_validated"),
                                "provider_effect_id": uuid4(),
                                "execution_context_ref": ref,
                            }
                        ),
                    }
                )
                run.agent_state = {"recovery_v1": initial.model_dump(mode="json")}
                await db_session.commit()

                async def commit(factory, *, previous, execution, context, phase="working"):
                    updated = previous.model_copy(
                        update={
                            "execution": execution,
                            "phase": phase,
                            "next_check_at": datetime.now(UTC) if phase != "working" else None,
                            "state_revision": previous.state_revision + 1,
                        }
                    )
                    async with factory() as session:
                        current = await session.get(GenerationRun, run.id, with_for_update=True)
                        assert ex.recovery_state(current) == previous
                        current.agent_state = {"recovery_v1": updated.model_dump(mode="json")}
                        await session.commit()
                    return updated

                monkeypatch.setattr(ex, "commit_execution_cursor", commit)
                snapshots = []

                def new_driver(current, context):
                    driver = ex.ExecutionDriver(factory, current, context, None)

                    async def save(execution, *, context, phase="working"):
                        # Falsifier: no source checkpoint until terminal receipt and
                        # fresh post-state/second incarnation check both completed.
                        if "pending_role" not in context.get("completed", {}):
                            assert len(reads) >= 2
                        snapshots.append(dict(await harness.handle.snapshot_files()))
                        driver.state = await commit(
                            factory,
                            previous=driver.state,
                            execution=execution,
                            context=context,
                            phase=phase,
                        )
                        driver.context = context

                    monkeypatch.setattr(driver, "save", save)
                    return driver

                if damage == "coordinator_tool":
                    from types import SimpleNamespace

                    from yleum_api.services import max_finalization as final
                    from yleum_api.services.generation.agent_runtime import checked_fast_check
                    driver = new_driver(initial, {"completed": {}})
                    coordinator = final.MaxFinalizationCoordinator(
                        session_factory=factory, generation_run_id=run.id,
                        project_id=run.project_id, project_slug="synthetic",
                        executor=harness.handle, continuation=driver,
                    )
                    runtime = SimpleNamespace(coordinator=coordinator)
                    action = Action("build", {}, "")
                    async def checked():
                        return await checked_fast_check(runtime, None)
                    with pytest.raises(ex.GenerationYielded):
                        await driver.tool("command-once", action, checked)
                    assert len(sent) == 1 and snapshots == []
                    async with factory() as session:
                        current = await session.get(GenerationRun, run.id)
                        resumed = ex.recovery_state(current).model_copy(
                            update={"phase": "working", "next_check_at": None})
                        current.agent_state = {"recovery_v1": resumed.model_dump(mode="json")}
                        await session.commit()
                    cold = new_driver(resumed, deepcopy(driver.context))
                    coordinator.continuation = cold
                    saved["acknowledge"] = True
                    command_changes.clear()
                    result = await cold.tool("command-once", action, checked)
                    assert result["ok"] is True
                    assert len(sent) == 3 and len({item["operation_id"] for item in sent}) == 3
                    assert cold.state.execution.next_tool_index == 1
                    assert snapshots[-1]["src/a.ts"] == "completed command"
                    return
                if damage == "coordinator":
                    from yleum_api.models.project_cell import ProjectCellActivityLease
                    from yleum_api.services import max_finalization as final
                    from yleum_api.services.generation.recovery_pipeline import (
                        reconcile_pending_role,
                    )
                    driver = new_driver(initial, {"completed": {}})
                    coordinator = final.MaxFinalizationCoordinator(
                        session_factory=factory, generation_run_id=run.id,
                        project_id=run.project_id,
                        project_slug="synthetic", executor=harness.handle, continuation=driver,
                    )
                    identity = await harness.handle.current_identity()
                    kwargs = dict(identity=identity, dimension=final.ProofDimension.FAST_CHECK,
                                  role=project_cell_executor.ProjectCellCommandRole.FAST_CHECK,
                                  phase=final.GenerationPhase.FAST_CHECK)
                    with pytest.raises(ex.GenerationYielded):
                        await coordinator._execute_role(**kwargs)
                    old_op = sent[0]["operation_id"]
                    async with factory() as session:
                        old_lease = await session.get(ProjectCellActivityLease, old_op)
                        assert old_lease.state == "active", "unknown ACK became failed activity"
                        current = await session.get(GenerationRun, run.id)
                        resumed = ex.recovery_state(current).model_copy(
                            update={"phase": "working", "next_check_at": None})
                        current.agent_state = {"recovery_v1": resumed.model_dump(mode="json")}
                        await session.commit()
                    cold = new_driver(resumed, deepcopy(driver.context))
                    await reconcile_pending_role(cold, harness.handle)
                    assert "pending_role" not in cold.context["completed"]
                    assert len(sent) == 1 and len(reads) >= 2
                    saved["acknowledge"] = True
                    command_changes.clear()  # The fresh compiler does not mutate source.
                    coordinator.continuation = cold
                    current_identity = await harness.handle.current_identity()
                    assert current_identity != identity
                    proof = await coordinator._proof(current_identity)
                    result = await coordinator._run_role_result(
                        **{**kwargs, "identity": current_identity}, proof=proof)
                    assert result.outcome == "green" and result.operation_id != old_op
                    assert len(sent) == 2 and sent[0]["operation_id"] != sent[1]["operation_id"]
                    async with factory() as session:
                        for call in sent:
                            lease = await session.get(
                                ProjectCellActivityLease, call["operation_id"],
                            )
                            assert lease.state == "completed"
                    return
                driver = new_driver(initial, {"schema_version": 1, "entry": "pipeline"})
                with pytest.raises(ex.GenerationYielded):
                    await driver.tool(
                        "command-once", action, lambda: harness.handle.execute(action),
                    )
                assert len(harness.exec_calls) == 1 and snapshots == []
                async with factory() as session:
                    current = await session.get(GenerationRun, run.id)
                    resumed = ex.recovery_state(current).model_copy(
                        update={"phase": "working", "next_check_at": None}
                    )
                    current.agent_state = {"recovery_v1": resumed.model_dump(mode="json")}
                    await session.commit()
                cold = new_driver(resumed, deepcopy(driver.context))
                result = await cold.tool(
                    "command-once", action, lambda: harness.handle.execute(action),
                )
                assert result["ok"] is True and len(harness.exec_calls) == 1
                expected = {".omnia/cell.json": '{"version":1}', "src/a.ts": "completed command"}
                assert snapshots == [expected]
                return
            async def execute():
                if is_role:
                    observation = await harness.handle.run_role(
                        project_cell_executor.ProjectCellCommandRole.FAST_CHECK, role_operation,
                    )
                    return {"ok": observation.ok, "detail": observation.redacted_detail}
                return await harness.handle.execute(action)
            if damage in {"role_foreign", "role_proof", "role_cancel", "role_expired"}:
                from yleum_api.services.generation.recovery_contract import RecoveryContractError
                lease = await db_session.get(ProjectCellActivityLease, role_operation)
                if damage == "role_foreign":
                    lease.generation_run_id = None
                elif damage == "role_proof":
                    lease.proof_key = "e" * 64
                elif damage == "role_cancel":
                    lease.state = "cancelled"
                else:
                    lease.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
                await db_session.commit()
                with tool_effect_scope(cursor), pytest.raises(RecoveryContractError):
                    await execute()
                assert sent == [] and harness.exec_calls == []
                return
            with tool_effect_scope(cursor):
                if is_role:
                    with pytest.raises(OrchestratorUnavailable):
                        await execute()
                    first = {"ok": False}
                else:
                    first = await execute()
            assert not first["ok"] and len(harness.exec_calls) == 1
            cold = ToolEffectCursor(claim, 0, 0, "command-once", reconcile_only=True)
            if damage == "role_cold_proof":
                from yleum_api.services.generation.recovery_contract import RecoveryContractError
                lease = await db_session.get(ProjectCellActivityLease, role_operation)
                lease.proof_key = "e" * 64
                await db_session.commit()
                with tool_effect_scope(cold), pytest.raises(RecoveryContractError):
                    await execute()
                assert len(sent) == 1 and reads == []
                return
            with tool_effect_scope(cold):
                result = await execute()
            assert result["ok"] is (damage in {None, "role"}), result
            assert len(harness.exec_calls) == len(sent) == 1
            if is_role:
                assert sent[0]["operation_id"] == role_operation
                assert saved["receipt"].operation_id == role_operation
            if damage is None:
                assert saved["tracked"] is True and len(reads) >= 2
                assert result["detail"] == "cell exec ok"
                assert result["files"]["src/a.ts"] == "completed command"
                assert (await harness.handle.snapshot_files())["src/a.ts"] == "completed command"
        finally:
            ex._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


async def test_executor_acquires_real_sql_repair_lease_after_resource_only_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from unittest.mock import AsyncMock

    from yleum_api.services import project_cell_capacity
    from yleum_api.services.orchestrator_client import OrchestratorUnavailable

    calls = []

    async def ensure(request):
        calls.append(("ensure", request))
        if len(calls) == 1:
            raise OrchestratorUnavailable("fixture lost ACK")
        return response(request)

    async def observe(request):
        calls.append(("observe", request))
        return response(request)

    def response(request):
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id,
            state="resources_ready",
            provider_ref="fixture",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    async def no_delay(*_):
        return None

    monkeypatch.setattr(project_cell_capacity.asyncio, "sleep", no_delay)
    client = SimpleNamespace(ensure=ensure, observe_resources=observe, control=AsyncMock())
    harness = await asyncio.wait_for(
        _prepare_executor(
            monkeypatch,
            db_session,
            test_engine,
            lifecycle_client=client,
            capacity_dispatch_token=uuid4(),
            expected_bootstrap_fence=3,
        ),
        20,
    )
    assert [kind for kind, _ in calls] == ["ensure", "observe", "ensure"]
    assert calls[0][1].operation_id != calls[2][1].operation_id
    assert calls[0][1].generation_run_id == calls[2][1].generation_run_id == harness.run_id
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        operations = list(
            await session.scalars(
                select(ProjectCellOperation)
                .where(
                    ProjectCellOperation.workspace_id == harness.workspace_id,
                )
                .order_by(ProjectCellOperation.fencing_epoch)
            )
        )
        assert [op.status for op in operations] == ["indeterminate", "completed", "completed"]
        assert (await session.get(GenerationRun, harness.run_id)).status == "running"
    assert not harness.write_calls and not harness.exec_calls


async def test_existing_workspace_bootstrap_waits_namespace_before_locking_run(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from yleum_api.services.project_cells import lock_workspace_operations

    owner = await _new_user(db_session, "bootstrap-lock")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, project, owner)
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
    )
    db_session.add(workspace)
    await db_session.commit()
    monkeypatch.setattr(project_cell_executor, "get_engine", lambda: test_engine)

    async def ready(*_, **__):
        return ProjectCellControlReadiness(
            selected=True, ready=True, provider="docker_owner_canary", reason="ready"
        )

    monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", ready)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    bootstrap = None
    try:
        async with factory() as repair:
            await lock_workspace_operations(repair, workspace.id)
            bootstrap = asyncio.create_task(
                project_cell_executor.maybe_create_project_cell_executor(
                    project_id=project.id,
                    project_slug=project.slug,
                    project_template="max_miniapp",
                    user_id=owner.id,
                    generation_run_id=run.id,
                    docs_media_execute=lambda _: None,
                )
            )

            async def wait_for_namespace():
                while not await repair.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                        "AND objid=(hashtext(:key)::bigint & 4294967295)::oid AND NOT granted)"
                    ),
                    {"key": str(workspace.id)},
                ):
                    if bootstrap.done():
                        await bootstrap
                        pytest.fail("bootstrap did not reach owning namespace")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_namespace(), 3)
            await repair.execute(text("SET LOCAL lock_timeout = '500ms'"))
            await repair.get(Project, project.id, with_for_update=True)
            await repair.get(GenerationRun, run.id, with_for_update=True)
            # No runtime/provider effect is allowed until the owning namespace is released.
            assert not bootstrap.done()
            bootstrap.cancel()
            await asyncio.gather(bootstrap, return_exceptions=True)
            await repair.rollback()
    finally:
        if bootstrap is not None and not bootstrap.done():
            bootstrap.cancel()
            await asyncio.gather(bootstrap, return_exceptions=True)
