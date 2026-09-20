from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from omnia_api.models.user import User
from omnia_api.services.max_finalization import (
    MaxFinalizationCoordinator,
    MaxFinalizationStatus,
)
from omnia_api.services.max_runtime_probe import MaxRuntimeProbe
from omnia_api.services.orchestrator_client import (
    ProjectCellAgentExecResponse,
    ProjectCellAgentOperationStatus,
    ProjectCellPreviewSession,
    ProjectCellWorkspaceIdentity,
)
from omnia_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
    ProjectCellExecutorHandle,
    ProjectCellPreviewSyncResult,
)
from omnia_api.services.project_cell_proofs import ProofDimension, ProofIdentity
from omnia_api.services.promotion_permit import (
    canonical_files_digest,
    workspace_revision_digest,
)


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


def _install_exact_release_probe(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    async def probe(preview, **_kwargs) -> MaxRuntimeProbe:
        digest = _RUNTIME_ARTIFACT_DIGESTS[preview.workspace_id]()
        return MaxRuntimeProbe(
            True,
            "signed preview auth and protected read green",
            digest,
            f"build/sha256/{digest}",
        )

    async def security(
        _base_url,
        *,
        bootstrap_url,
        require_embedded_framing,
        framing_policy,
    ):
        from omnia_api.services.security_gate import surface_verdict_from_headers

        assert bootstrap_url.endswith("&signature=" + "a" * 64)
        assert require_embedded_framing is True
        protected_headers = {"x-content-type-options": "nosniff"}
        document_headers = {
            "x-content-type-options": "nosniff",
            "content-security-policy": framing_policy,
        }
        return surface_verdict_from_headers(
            protected_headers,
            require_embedded_framing=True,
            protected_status=200,
            framing_policy=framing_policy,
            document_headers=document_headers,
            document_status=200,
        )

    monkeypatch.setattr(
        "omnia_api.services.max_runtime_probe.probe_max_cell_runtime",
        probe,
    )
    monkeypatch.setattr(
        "omnia_api.services.security_gate.run_security_gate",
        security,
    )
    return _RUNTIME_ARTIFACT_DIGESTS.clear


@pytest.fixture(autouse=True)
def _exact_release_probe(monkeypatch: pytest.MonkeyPatch):
    cleanup = _install_exact_release_probe(monkeypatch)
    yield
    cleanup()


_RUNTIME_ARTIFACT_DIGESTS: dict[UUID, Callable[[], str]] = {}


@dataclass
class _Harness:
    coordinator: MaxFinalizationCoordinator
    roles: list[ProjectCellCommandRole]
    runtime_probes: list[str]
    set_build_green: Callable[[bool], None]
    files: dict[str, str]


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

    files = _files()
    identity = ProofIdentity(
        workspace_id=workspace.id,
        generation_run_id=run.id,
        fencing_epoch=7,
        workspace_revision=workspace_revision_digest(files),
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64,
        toolchain_digest="6" * 64,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest="7" * 64,
    )
    roles: list[ProjectCellCommandRole] = []
    runtime_probes: list[str] = []
    state = {"build_green": build_green}
    observations: dict[UUID, ProjectCellCommandObservation] = {}

    def active_identity() -> ProofIdentity:
        return replace(identity, workspace_revision=workspace_revision_digest(files))

    async def current_identity() -> ProofIdentity:
        return active_identity()

    async def run_role(
        role: ProjectCellCommandRole,
        operation_id: UUID,
    ) -> ProjectCellCommandObservation:
        roles.append(role)
        ok = role is not ProjectCellCommandRole.FULL_BUILD or state["build_green"]
        current = active_identity()
        observation = ProjectCellCommandObservation(
            operation_id=operation_id,
            role=role,
            ok=ok,
            timed_out=False,
            redacted_detail="green" if ok else "TS2322",
            before=current,
            after=current,
            invalidated_dimensions=frozenset(),
        )
        observations[operation_id] = observation
        return observation

    async def runtime_probe(proof_key: str) -> MaxRuntimeProbe:
        runtime_probes.append(proof_key)
        digest = canonical_files_digest(files)
        return MaxRuntimeProbe(True, "runtime green", digest, f"build/sha256/{digest}")

    def wire_identity(value: ProofIdentity) -> ProjectCellWorkspaceIdentity:
        return ProjectCellWorkspaceIdentity(
            workspace_revision=value.workspace_revision,
            dependency_digest=value.dependency_digest,
            schema_data_digest=value.schema_data_digest,
            cell_manifest_digest=value.cell_manifest_digest,
            environment_digest=value.base_image_digest,
            build_config_digest=value.build_config_digest,
        )

    async def operation_status(operation_id: UUID) -> ProjectCellAgentOperationStatus:
        observation = observations[operation_id]
        now = datetime.now(UTC)
        before = wire_identity(observation.before)
        after = wire_identity(observation.after)
        response = ProjectCellAgentExecResponse(
            ok=observation.ok,
            exit_code=0 if observation.ok else 1,
            detail=observation.redacted_detail,
            timed_out=observation.timed_out,
            workspace_revision=observation.after.workspace_revision,
            operation_id=operation_id,
            before_identity=before,
            after_identity=after,
            environment_mutated=before != after,
        )
        return ProjectCellAgentOperationStatus(
            operation_id=operation_id,
            state=(
                "timed_out"
                if observation.timed_out
                else "completed"
                if observation.ok
                else "failed"
            ),
            phase=observation.role.value if observation.role is not None else "command",
            started_at=now - timedelta(seconds=1),
            deadline_at=now + timedelta(minutes=1),
            heartbeat_at=now,
            log_bytes=len(observation.redacted_detail.encode()),
            terminal_response=response,
        )

    async def replay_role_response(
        role: ProjectCellCommandRole,
        response: ProjectCellAgentExecResponse,
        expected: ProofIdentity,
    ) -> ProjectCellCommandObservation:
        assert response.operation_id is not None
        observation = observations[response.operation_id]
        assert observation.role is role
        assert observation.before == expected
        return observation

    async def noop() -> None:
        return None

    async def create_preview_session() -> ProjectCellPreviewSession:
        suffix = get_settings().project_cell_preview_host_suffix
        preview_url = f"https://cell-{workspace.id.hex[:12]}-dev.{suffix}"
        return ProjectCellPreviewSession(
            workspace_id=workspace.id,
            preview_url=preview_url,
            bootstrap_url=(
                f"{preview_url}/"
                "api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
            ),
            expires_at="2100-01-01T00:00:00+00:00",
        )

    async def snapshot_files() -> dict[str, str]:
        return dict(files)

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
        create_preview_session=create_preview_session,
        release=noop,
        current_identity=current_identity,
        run_role=run_role,
        replay_role_response=replay_role_response,
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

    _RUNTIME_ARTIFACT_DIGESTS[workspace.id] = lambda: canonical_files_digest(files)
    return _Harness(coordinator, roles, runtime_probes, set_build_green, files)


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
    assert first.proof.permit is not None
    assert second.proof.permit == first.proof.permit
    assert second.checkpoint.candidate_id == first.checkpoint.candidate_id


async def test_resume_rebuilds_legacy_full_build_and_dependent_proofs(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch,
) -> None:
    from omnia_api.services import release_proof

    release_calls = 0
    real_release_proof = release_proof.run_release_proof

    async def counted_release(*args, **kwargs):
        nonlocal release_calls
        release_calls += 1
        return await real_release_proof(*args, **kwargs)

    monkeypatch.setattr(release_proof, "run_release_proof", counted_release)
    harness = await _new_harness(db_session, test_engine)
    first = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")

    result = await db_session.scalar(
        select(ProjectCellProofResult).where(
            ProjectCellProofResult.dimension == ProofDimension.FULL_BUILD.value
        )
    )
    assert result is not None
    runtime_result = await db_session.scalar(
        select(ProjectCellProofResult).where(
            ProjectCellProofResult.dimension == ProofDimension.RUNTIME.value
        )
    )
    assert runtime_result is not None

    identity = await harness.coordinator.executor.current_identity()
    legacy_build_operation = uuid.uuid5(
        harness.coordinator.generation_run_id,
        "command:"
        f"{identity.workspace_id}:{identity.fencing_epoch}:{identity.proof_key}:"
        f"{ProjectCellCommandRole.FULL_BUILD.value}",
    )
    legacy_runtime_operation = uuid.uuid5(
        harness.coordinator.generation_run_id,
        f"runtime:{runtime_result.dimension_key}",
    )
    build_lease = await db_session.get(ProjectCellActivityLease, result.operation_id)
    runtime_lease = await db_session.get(ProjectCellActivityLease, runtime_result.operation_id)
    assert build_lease is not None and runtime_lease is not None
    build_lease.operation_id = legacy_build_operation
    runtime_lease.operation_id = legacy_runtime_operation
    result.operation_id = legacy_build_operation
    runtime_result.operation_id = legacy_runtime_operation
    result.redacted_detail = "legacy unversioned build evidence"
    await db_session.commit()

    second = await harness.coordinator.resume(first.checkpoint)
    third = await harness.coordinator.resume(second.checkpoint)

    assert second.status is third.status is MaxFinalizationStatus.COMPLETE
    assert harness.roles.count(ProjectCellCommandRole.FULL_BUILD) == 2
    assert len(harness.runtime_probes) == 2
    assert release_calls == 2


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

    incomplete = {".omnia/cell.json": "{}"}
    harness.files.clear()
    harness.files.update(incomplete)
    outcome = await harness.coordinator.finalize(
        files=incomplete,
        prompt="Build tracker",
    )

    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert harness.roles == []


async def test_red_exact_behavior_creates_zero_candidates(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from dataclasses import replace

    harness = await _new_harness(db_session, test_engine)

    async def red_behavior(_proof_key: str) -> MaxRuntimeProbe:
        return MaxRuntimeProbe(False, "candidate behavior is red")

    harness.coordinator.executor = replace(
        harness.coordinator.executor,
        runtime_probe=red_behavior,
    )
    outcome = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")

    assert outcome.status is MaxFinalizationStatus.FAILED
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        assert (await session.scalar(select(func.count(ProjectCellCandidate.id)))) == 0


async def test_red_release_proof_creates_zero_candidates(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch,
) -> None:
    from omnia_api.services.functional_gate import Check, FunctionalVerdict

    harness = await _new_harness(db_session, test_engine)

    async def red_release(*_args, **_kwargs) -> FunctionalVerdict:
        return FunctionalVerdict(
            passed=False,
            checks=[Check("max_data_plane", False, "release behavior is red")],
            summary="release behavior is red",
        )

    monkeypatch.setattr("omnia_api.services.release_proof.run_release_proof", red_release)
    outcome = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")

    assert outcome.status is MaxFinalizationStatus.FAILED
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        assert (await session.scalar(select(func.count(ProjectCellCandidate.id)))) == 0


async def test_stale_identity_blocks_candidate_prepare(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    import pytest

    from omnia_api.services.promotion_permit import PromotionPermitError

    harness = await _new_harness(db_session, test_engine)
    original = await harness.coordinator.executor.current_identity()
    stale = replace(original, workspace_revision="9" * 64)
    calls = 0

    async def identity_changes_before_promotion() -> ProofIdentity:
        nonlocal calls
        calls += 1
        # Initial, post-build and post-release file reads stay on one fence.
        # The first promotion read observes the stale revision.
        return original if calls <= 3 else stale

    harness.coordinator.executor = replace(
        harness.coordinator.executor,
        current_identity=identity_changes_before_promotion,
    )

    with pytest.raises(PromotionPermitError) as raised:
        await harness.coordinator.finalize(files=_files(), prompt="Build tracker")

    assert raised.value.code == "PROMOTION_PERMIT_STALE"
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        assert (await session.scalar(select(func.count(ProjectCellCandidate.id)))) == 0


async def test_changed_full_envelope_does_not_reuse_failed_bootstrap_command(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from dataclasses import replace

    import pytest

    from omnia_api.services.generation_metrics import GenerationPhase
    from omnia_api.services.project_cell_proofs import ProofDimension

    harness = await _new_harness(db_session, test_engine)
    coordinator = harness.coordinator
    original = await coordinator.executor.current_identity()
    changed = replace(original, workspace_revision="9" * 64)
    assert original.dimension_key(ProofDimension.BOOTSTRAP) == changed.dimension_key(
        ProofDimension.BOOTSTRAP
    )
    operations = []

    async def command(role, operation_id):
        operations.append(operation_id)
        if len(operations) == 1:
            raise RuntimeError("first transport interruption")
        return ProjectCellCommandObservation(
            operation_id, role, True, False, "green", changed, changed, frozenset()
        )

    coordinator.executor = replace(coordinator.executor, run_role=command)
    with pytest.raises(RuntimeError, match="first transport interruption"):
        await coordinator._execute_role(
            identity=original,
            dimension=ProofDimension.BOOTSTRAP,
            role=ProjectCellCommandRole.BOOTSTRAP,
            phase=GenerationPhase.PREPARE,
        )
    result = await coordinator._execute_role(
        identity=changed,
        dimension=ProofDimension.BOOTSTRAP,
        role=ProjectCellCommandRole.BOOTSTRAP,
        phase=GenerationPhase.PREPARE,
    )
    assert result.ok
    assert len(set(operations)) == 2


async def test_persisted_fatal_bootstrap_blocks_source_repair_after_restart(
    db_session, test_engine
):
    from dataclasses import replace

    import pytest

    from omnia_api.services.orchestrator_client import OrchestratorBadRequest

    harness = await _new_harness(db_session, test_engine)
    calls = []

    async def command(role, operation_id):
        calls.append(role)
        raise OrchestratorBadRequest(
            "private diagnostic",
            status_code=409,
            upstream_code="protected_environment_recovery_required",
        )

    coordinator = harness.coordinator
    coordinator.executor = replace(coordinator.executor, run_role=command)
    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await coordinator.fast_check()
    restarted = MaxFinalizationCoordinator(
        session_factory=coordinator.session_factory,
        generation_run_id=coordinator.generation_run_id,
        project_id=coordinator.project_id,
        project_slug=coordinator.project_slug,
        executor=coordinator.executor,
    )

    async def incomplete_source():
        return {".omnia/cell.json": "{}"}

    restarted.executor = replace(restarted.executor, snapshot_files=incomplete_source)

    async def source_repair(*args):
        calls.append("MODEL REPAIR")

    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await restarted.finalize_with_repair(prompt="Build tracker", repair=source_repair)
    assert calls == [ProjectCellCommandRole.BOOTSTRAP]


async def test_source_change_keeps_bootstrap_dimension_cache_but_rechecks_source(
    db_session, test_engine
):
    from dataclasses import replace

    harness = await _new_harness(db_session, test_engine)
    coordinator = harness.coordinator
    current = await coordinator.executor.current_identity()
    calls = []

    async def identity():
        return current

    async def command(role, operation_id):
        calls.append((role, operation_id))
        return ProjectCellCommandObservation(
            operation_id, role, True, False, "green", current, current, frozenset()
        )

    coordinator.executor = replace(
        coordinator.executor, current_identity=identity, run_role=command
    )
    first = await coordinator.fast_check()
    current = replace(current, workspace_revision="9" * 64)
    second = await coordinator.fast_check()
    third = await coordinator.fast_check()
    assert first.outcome == second.outcome == third.outcome == "green"
    assert [role for role, _ in calls] == [
        ProjectCellCommandRole.BOOTSTRAP,
        ProjectCellCommandRole.FAST_CHECK,
        ProjectCellCommandRole.FAST_CHECK,
    ]
    assert len({operation for _, operation in calls}) == 3


_CLIENTS_ROUTE = (
    "export async function GET() { return Response.json([]) }\n"
    "export async function POST() { return Response.json({}, { status: 201 }) }\n"
)
_VISITS_ROUTE = "export async function GET() { return Response.json([]) }\n"


async def _adaptation_run(db_session: AsyncSession, harness: _Harness) -> None:
    """Mark the run as a restoration adaptation of a draft that served visits."""
    import asyncio

    from omnia_api.models.snapshot import Snapshot
    from omnia_api.services import repo

    coordinator = harness.coordinator
    before = {
        **_files(),
        "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
        "src/app/api/visits/route.ts": _VISITS_ROUTE,
        "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
    }
    sha = await asyncio.to_thread(repo.init_from_files, coordinator.project_id, before, "v2")
    snapshot = Snapshot(project_id=coordinator.project_id, commit_sha=sha, prompt_text="v2")
    db_session.add(snapshot)
    await db_session.flush()
    run = await db_session.get(GenerationRun, coordinator.generation_run_id)
    assert run is not None
    run.agent_state = {"restoration_adaptation": {"base_draft_snapshot_id": str(snapshot.id)}}
    await db_session.commit()


async def test_adaptation_that_drops_a_draft_route_returns_to_edit(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)
    await _adaptation_run(db_session, harness)

    # The adapted v1 screens came back, but reading visits disappeared. The
    # untouched kit route is not owned by the app and must not be reported.
    candidate = {
        **_files(),
        "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
        "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
    }
    harness.files.clear()
    harness.files.update(candidate)
    outcome = await harness.coordinator.finalize(
        files=candidate,
        prompt="Верни экраны выбранной исторической версии",
    )

    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert "GET /api/visits" in outcome.redacted_detail
    assert "/api/omnia" not in outcome.redacted_detail  # platform routes are not owned
    assert harness.roles == []  # no build runs on a result that lost a function


async def test_adaptation_keeping_every_route_proceeds_to_the_build(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)
    await _adaptation_run(db_session, harness)

    candidate = {
        **_files(),
        "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
        "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
        # Moved into a route group: still the same GET /api/visits.
        "src/app/(data)/api/visits/route.ts": (
            "export const GET = async () => Response.json([])"
        ),
    }
    harness.files.clear()
    harness.files.update(candidate)
    outcome = await harness.coordinator.finalize(
        files=candidate,
        prompt="Верни экраны выбранной исторической версии",
    )

    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert ProjectCellCommandRole.FULL_BUILD in harness.roles


async def test_ordinary_generation_is_not_capability_checked(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)
    # No restoration_adaptation marker: removing routes is a normal edit.
    outcome = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")
    assert outcome.status is MaxFinalizationStatus.COMPLETE
