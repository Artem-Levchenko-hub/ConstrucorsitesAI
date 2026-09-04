from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.max_finalization import (
    MaxFinalizationCoordinator,
    MaxFinalizationStatus,
)
from omnia_api.services.max_runtime_probe import MaxRuntimeProbe
from omnia_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
    ProjectCellExecutorHandle,
    ProjectCellPreviewSyncResult,
)
from omnia_api.services.project_cell_proofs import ProofIdentity


def _files() -> dict[str, str]:
    return {
        ".omnia/cell.json": json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "name": "final-test",
                        "role": "full_build",
                        "argv": ["pnpm", "test"],
                    }
                ],
                "services": [{"name": "web", "argv": ["pnpm", "start"]}],
                "routes": [{"path": "/", "service": "web", "port": 3000}],
            }
        ),
        "src/app/page.tsx": (
            "export default function Page() { return <main>Real complete product</main> }"
        ),
    }


@dataclass
class _Harness:
    coordinator: MaxFinalizationCoordinator
    roles: list[ProjectCellCommandRole]
    set_build_green: Callable[[bool], None]


async def _new_harness(
    session: AsyncSession,
    engine: AsyncEngine,
    *,
    build_green: bool = True,
) -> _Harness:
    owner = User(
        email=f"finalization-{uuid.uuid4().hex}@example.com",
        password_hash="x",
    )
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Finalization",
        slug=f"finalization-{uuid.uuid4().hex}",
        template="max_miniapp",
    )
    session.add(project)
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"finalization:{uuid.uuid4().hex}",
        prompt_hash="a" * 64,
        status="running",
        agent_state={},
    )
    session.add(run)
    await session.flush()
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    session.add(workspace)
    await session.commit()

    identity = ProofIdentity(
        workspace_id=workspace.id,
        generation_run_id=run.id,
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
    roles: list[ProjectCellCommandRole] = []
    state = {"build_green": build_green}

    async def current_identity() -> ProofIdentity:
        return identity

    async def run_role(
        role: ProjectCellCommandRole,
        operation_id: UUID,
    ) -> ProjectCellCommandObservation:
        roles.append(role)
        ok = role is not ProjectCellCommandRole.FULL_BUILD or state["build_green"]
        return ProjectCellCommandObservation(
            operation_id=operation_id,
            role=role,
            ok=ok,
            timed_out=False,
            redacted_detail="green" if ok else "TS2322",
            before=identity,
            after=identity,
            invalidated_dimensions=frozenset(),
        )

    async def runtime_probe(_proof_key: str) -> MaxRuntimeProbe:
        return MaxRuntimeProbe(True, "runtime green", "8" * 64, "runtime/sha256/" + "8" * 64)

    async def operation_status(_operation_id: UUID):
        raise AssertionError("instant command must finish before its first heartbeat")

    async def noop() -> None:
        return None

    async def snapshot_files() -> dict[str, str]:
        return _files()

    async def stage_patch(_writes: dict[str, str], _deletes: tuple[str, ...]) -> None:
        return None

    async def stage_files(_writes: dict[str, str]) -> None:
        return None

    async def sync_preview() -> ProjectCellPreviewSyncResult:
        return ProjectCellPreviewSyncResult({}, None)

    async def legacy_execute(_action: object) -> dict[str, object]:
        return {"ok": True}

    handle = ProjectCellExecutorHandle(
        execute=legacy_execute,  # type: ignore[arg-type]
        sync_preview=sync_preview,
        snapshot_files=snapshot_files,
        stage_patch=stage_patch,
        stage_files=stage_files,
        apply_external_files=stage_files,
        export_files=snapshot_files,
        workspace_id=workspace.id,
        create_preview_session=noop,  # type: ignore[arg-type]
        release=noop,
        current_identity=current_identity,
        run_role=run_role,
        runtime_probe=runtime_probe,
        operation_status=operation_status,
        capabilities={"portable_machine": True},
        is_portable=lambda: True,
    )
    coordinator = MaxFinalizationCoordinator(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        generation_run_id=run.id,
        project_id=project.id,
        project_slug=project.slug,
        executor=handle,
    )

    def set_build_green(value: bool) -> None:
        state["build_green"] = value

    return _Harness(coordinator, roles, set_build_green)


async def test_finalize_runs_one_full_build_and_reuses_release_evidence(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)

    first = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")
    second = await harness.coordinator.resume(first.checkpoint)

    assert first.status is second.status is MaxFinalizationStatus.COMPLETE
    assert harness.roles == [
        ProjectCellCommandRole.BOOTSTRAP,
        ProjectCellCommandRole.FULL_BUILD,
    ]
    assert first.proof.full_build is not None
    assert first.proof.runtime is not None
    assert first.proof.release is not None
    assert second.checkpoint.candidate_id == first.checkpoint.candidate_id


async def test_unchanged_red_build_is_terminal_without_retry(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine, build_green=False)

    first = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")
    harness.set_build_green(True)
    second = await harness.coordinator.resume(first.checkpoint)

    assert first.status is second.status is MaxFinalizationStatus.FAILED
    assert harness.roles.count(ProjectCellCommandRole.FULL_BUILD) == 1
    assert "TS2322" in second.redacted_detail


async def test_source_gap_returns_to_edit_without_running_commands(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)

    outcome = await harness.coordinator.finalize(
        files={".omnia/cell.json": "{}"},
        prompt="Build tracker",
    )

    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert harness.roles == []
