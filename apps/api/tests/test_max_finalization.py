from __future__ import annotations

import asyncio
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
from omnia_api.models.restoration import Restoration
from omnia_api.models.user import User
from omnia_api.services.max_finalization import (
    AdaptationActivationRecoveryRequired,
    MaxFinalizationCoordinator,
    MaxFinalizationStatus,
    ProofBundle,
    _adaptation_proof_capability_gap,
)
from omnia_api.services.max_runtime_probe import MaxRuntimeProbe
from omnia_api.services.orchestrator_client import (
    ProjectCellAgentExecResponse,
    ProjectCellAgentOperationStatus,
    ProjectCellPreviewSession,
    ProjectCellWorkspaceIdentity,
    RestorationAdaptationProof,
    RestorationAdaptationWorkspace,
)
from omnia_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
    ProjectCellExecutorHandle,
    ProjectCellPreviewSyncResult,
)
from omnia_api.services.project_cell_proofs import ProofDimension, ProofIdentity, ProofOutcome
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
        ".omnia/restoration-probe.json": json.dumps(
            {
                "version": 1,
                "endpoint": "/api/restoration-probe",
                "witnesses": [
                    {
                        "entity": "qa_tasks",
                        "id_column": "id",
                        "owner_column": "max_user_id",
                        "value_column": "title",
                        "create_values": {"status": "pending"},
                    }
                ],
                "max_payload_bytes": 4096,
            }
        ),
        "src/app/api/restoration-probe/route.ts": (
            "export async function GET() { return Response.json({items: [], complete: true}) }\n"
            "export async function POST() { return Response.json({item: {}}, {status: 201}) }\n"
        ),
        "src/app/api/restoration-probe/[...path]/route.ts": (
            "export async function GET() { return Response.json({item: {}}) }\n"
            "export async function PATCH() { return Response.json({item: {}}) }\n"
            "export async function DELETE() { return Response.json({deleted: true}) }\n"
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
_CURRENT_ADAPTATION_DIFF = {
    "version": 1,
    "historical_source": "selected_historical_code",
    "current_source": "controller_observed_catalog",
    "findings": [],
    "blockers": [],
}


def test_adaptation_capabilities_are_controller_attested_and_fail_closed() -> None:
    from omnia_api.services.restoration_adaptation import _preservation_contract

    bundle = {
        "version": 2,
        "preservation_contract": _preservation_contract(),
        "data_contract_diff": _CURRENT_ADAPTATION_DIFF,
    }
    assert _adaptation_proof_capability_gap(bundle, {}) == (
        "adaptation_proof_unavailable: isolated database copy"
    )
    copy_only = {"restoration_adaptation_database_copy_v1": True}
    assert _adaptation_proof_capability_gap(bundle, copy_only) == (
        "adaptation_proof_unavailable: schema, CRUD, reload and owner isolation"
    )
    assert (
        _adaptation_proof_capability_gap(
            bundle,
            {
                **copy_only,
                "restoration_adaptation_proof_v1": True,
            },
        )
        is None
    )
    assert (
        _adaptation_proof_capability_gap(
            {**bundle, "version": 1},
            {
                **copy_only,
                "restoration_adaptation_proof_v1": True,
            },
        )
        == "adaptation_proof_unavailable: immutable preservation contract"
    )


def test_adaptation_transient_evidence_keeps_candidate_identity() -> None:
    identity = ProofIdentity(
        workspace_id=uuid.uuid4(),
        generation_run_id=uuid.uuid4(),
        fencing_epoch=3,
        workspace_revision="1" * 64,
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64,
        toolchain_digest="6" * 64,
        resource_profile_version="verification-v1",
        build_config_digest="7" * 64,
    )
    proof = MaxFinalizationCoordinator._transient_proof(identity)
    operation_id = uuid.uuid4()
    artifact_digest = "8" * 64
    result = MaxFinalizationCoordinator._transient_result(
        identity=identity,
        proof=proof,
        dimension=ProofDimension.RUNTIME,
        outcome=ProofOutcome.GREEN,
        operation_id=operation_id,
        detail="candidate runtime green",
        artifact_ref="verification/sha256/" + "9" * 64,
        artifact_digest=artifact_digest,
    )

    assert proof.workspace_id == identity.workspace_id
    assert proof.proof_key == identity.proof_key
    assert result.workspace_id == identity.workspace_id
    assert result.proof_id == proof.id
    assert result.dimension_key == identity.dimension_key(
        ProofDimension.RUNTIME,
        artifact_digest=artifact_digest,
    )


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
                f"{preview_url}/api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
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


async def test_generation_deadline_terminalizes_bound_adaptation(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from omnia_api.models.restoration import Restoration
    from omnia_api.services.max_finalization import watch_generation_deadline

    harness = await _new_harness(db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run.id)
    )
    assert workspace is not None
    operation = Restoration(
        id=uuid.uuid4(),
        project_id=run.project_id,
        owner_id=run.user_id,
        workspace_id=workspace.id,
        source_version_id=uuid.uuid4(),
        source_snapshot_id=uuid.uuid4(),
        base_draft_snapshot_id=uuid.uuid4(),
        target_commit_sha="a" * 40,
        base_commit_sha="b" * 40,
        idempotency_key=f"deadline-adaptation-{run.id}",
        request_digest="c" * 64,
        selected_branch="adaptive",
        adaptation_run_id=run.id,
        state="adapting",
        phase="generation",
        revision=2,
        fencing_epoch=workspace.fencing_epoch,
        request_payload={},
    )
    db_session.add(operation)
    run.agent_state = {
        "restoration_adaptation": {
            "operation_id": str(operation.id),
            "adaptation_run_id": str(run.id),
        }
    }
    await db_session.commit()

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    expired = await watch_generation_deadline(
        session_factory=factory,
        generation_run_id=run.id,
        now=datetime.now(UTC) + timedelta(hours=1),
    )

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert expired is True
    assert run.status == "failed"
    # Terminal callbacks hold the run row. Restoration terminalization is
    # deferred to the canonical project -> restoration -> run retry transaction.
    assert operation.state == "adapting"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"
    assert operation.phase == "generation"


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


async def _adaptation_run(db_session: AsyncSession, harness: _Harness) -> UUID:
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
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run.id)
    )
    project = await db_session.get(Project, run.project_id)
    assert workspace is not None and project is not None
    project.current_snapshot_id = snapshot.id
    operation = Restoration(
        id=uuid.uuid4(),
        project_id=run.project_id,
        owner_id=run.user_id,
        workspace_id=workspace.id,
        source_version_id=uuid.uuid4(),
        source_snapshot_id=snapshot.id,
        base_draft_snapshot_id=snapshot.id,
        target_commit_sha=sha,
        base_commit_sha=sha,
        idempotency_key=f"max-adaptation-{run.id}",
        request_digest="f" * 64,
        selected_branch="adaptive",
        adaptation_run_id=run.id,
        state="adapting",
        phase="generation",
        revision=2,
        fencing_epoch=workspace.fencing_epoch,
        request_payload={},
    )
    db_session.add(operation)
    from omnia_api.services.restoration_adaptation import _preservation_contract

    run.agent_state = {
        "restoration_adaptation": {
            "version": 2,
            "operation_id": str(operation.id),
            "project_id": str(project.id),
            "owner_id": str(run.user_id),
            "adaptation_run_id": str(run.id),
            "base_draft_snapshot_id": str(snapshot.id),
            "preservation_contract": _preservation_contract(),
            "data_contract_diff": _CURRENT_ADAPTATION_DIFF,
        }
    }
    harness.coordinator.executor = replace(
        harness.coordinator.executor,
        capabilities={
            **harness.coordinator.executor.capabilities,
            "restoration_adaptation_database_copy_v1": True,
            "restoration_adaptation_proof_v1": True,
        },
        restoration_adaptation_workspace=RestorationAdaptationWorkspace(
            source_workspace_id=workspace.id,
            candidate_workspace_id=workspace.id,
            operation_id=operation.id,
            project_id=project.id,
            owner_id=run.user_id,
            generation_run_id=run.id,
            candidate_fencing_epoch=workspace.fencing_epoch,
            source_database_digest="1" * 64,
            proof_digest="2" * 64,
            capabilities={
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
            },
        ),
    )
    await db_session.commit()
    return operation.id


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await _new_harness(db_session, test_engine)
    await _adaptation_run(db_session, harness)

    candidate = {
        **_files(),
        "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
        "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
        # Moved into a route group: still the same GET /api/visits.
        "src/app/(data)/api/visits/route.ts": ("export const GET = async () => Response.json([])"),
    }
    harness.files.clear()
    harness.files.update(candidate)
    from omnia_api.services import restorations

    async def activate(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(restorations, "activate_restoration_adaptation", activate)
    outcome = await harness.coordinator.finalize(
        files=candidate,
        prompt="Верни экраны выбранной исторической версии",
    )

    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert ProjectCellCommandRole.FULL_BUILD in harness.roles


async def test_adaptation_completion_callback_is_idempotent_after_activation_terminalizes_run(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from omnia_api.services.generation_metrics import GenerationPhase

    harness = await _new_harness(db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    run.status = "completed"
    run.agent_state = {
        "restoration_adaptation_activation": {
            "state": "completed",
            "publication_consumed": True,
        }
    }
    await db_session.commit()
    identity = await harness.coordinator.executor.current_identity()  # type: ignore[misc]
    proof = harness.coordinator._transient_proof(identity)

    outcome = await harness.coordinator._outcome(
        MaxFinalizationStatus.COMPLETE,
        harness.coordinator._checkpoint(identity, GenerationPhase.PROMOTE),
        ProofBundle(identity=proof),
        "adaptation activation completed",
    )

    await db_session.refresh(run)
    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert run.status == "completed"
    assert run.agent_state["restoration_adaptation_activation"]["publication_consumed"] is True


async def test_adaptation_cancel_callback_is_idempotent_after_terminal_receipt(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from omnia_api.services.generation_metrics import GenerationPhase

    harness = await _new_harness(db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    run.status = "cancelled"
    run.agent_state = {
        "restoration_adaptation_activation": {
            "state": "cancelled",
            "publication_consumed": False,
        }
    }
    await db_session.commit()
    identity = await harness.coordinator.executor.current_identity()  # type: ignore[misc]
    proof = harness.coordinator._transient_proof(identity)

    outcome = await harness.coordinator._outcome(
        MaxFinalizationStatus.CANCELLED,
        harness.coordinator._checkpoint(identity, GenerationPhase.PROMOTE),
        ProofBundle(identity=proof),
        "adaptation activation cancelled",
    )

    await db_session.refresh(run)
    assert outcome.status is MaxFinalizationStatus.CANCELLED
    assert run.status == "cancelled"


async def test_adaptation_without_trusted_copy_and_proof_capability_fails_before_build(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.services import repo
    from omnia_api.services.restoration_adaptation import _preservation_contract

    harness = await _new_harness(db_session, test_engine)
    sha = await asyncio.to_thread(
        repo.init_from_files,
        harness.coordinator.project_id,
        _files(),
        "v2",
    )
    snapshot = Snapshot(
        project_id=harness.coordinator.project_id,
        commit_sha=sha,
        prompt_text="v2",
    )
    db_session.add(snapshot)
    await db_session.flush()
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    run.agent_state = {
        "restoration_adaptation": {
            "version": 2,
            "base_draft_snapshot_id": str(snapshot.id),
            "preservation_contract": _preservation_contract(),
            "data_contract_diff": _CURRENT_ADAPTATION_DIFF,
        }
    }
    await db_session.commit()

    outcome = await harness.coordinator.finalize(
        files=_files(),
        prompt="Верни выбранную версию",
    )

    assert outcome.status is MaxFinalizationStatus.FAILED
    assert outcome.redacted_detail == "adaptation_proof_unavailable: isolated database copy"
    assert harness.roles == []


async def test_db_only_repair_uses_next_trusted_proof_attempt(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await _new_harness(db_session, test_engine)
    operation_id = await _adaptation_run(db_session, harness)
    harness.files.update(
        {
            "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
            "src/app/api/visits/route.ts": _VISITS_ROUTE,
            "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
        }
    )
    identity = await harness.coordinator.executor.current_identity()  # type: ignore[misc]
    attempts: list[int] = []
    receipts: list[RestorationAdaptationProof] = []

    async def prove(
        candidate: ProofIdentity,
        artifact_digest: str,
        proof_attempt: int,
    ) -> RestorationAdaptationProof:
        attempts.append(proof_attempt)
        ready = proof_attempt == 2
        receipt = RestorationAdaptationProof(
            state="proof_ready" if ready else "migration_required",
            reason_code=None if ready else "candidate_business_data_changed",
            source_workspace_id=candidate.workspace_id,
            candidate_workspace_id=candidate.workspace_id,
            operation_id=operation_id,
            project_id=harness.coordinator.project_id,
            owner_id=(
                await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
            ).user_id,
            generation_run_id=candidate.generation_run_id,
            candidate_fencing_epoch=candidate.fencing_epoch,
            proof_attempt=proof_attempt,
            source_workspace_revision=candidate.workspace_revision,
            candidate_workspace_revision=candidate.workspace_revision,
            candidate_proof_key=candidate.proof_key,
            candidate_artifact_digest=artifact_digest,
            source_database_digest="1" * 64,
            candidate_database_digest="1" * 64 if ready else "9" * 64,
            source_schema_digest="2" * 64,
            candidate_schema_digest="2" * 64,
            source_business_digest="3" * 64,
            candidate_business_digest="3" * 64 if ready else "8" * 64,
            source_technical_digest="4" * 64,
            candidate_technical_digest="4" * 64,
            probe_contract_digest="5" * 64,
            probe_rehearsal_digest="6" * 64,
            probe_rehearsal_database_digest="7" * 64,
            candidate_source_manifest_digest="8" * 64,
            proof_digest=f"{proof_attempt:064x}",
            capabilities={
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
                **({"restoration_adaptation_proof_v1": True} if ready else {}),
            },
        )
        receipts.append(receipt)
        return receipt

    harness.coordinator.executor = replace(
        harness.coordinator.executor,
        prove_restoration_adaptation=prove,
        capabilities={
            "portable_machine": True,
            "database_admin": "isolated_copy",
            "restoration_adaptation_database_copy_v1": True,
        },
    )
    from omnia_api.services import restorations

    async def activate(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(restorations, "activate_restoration_adaptation", activate)

    first = await harness.coordinator.finalize(files=harness.files, prompt="Adapt")
    second = await harness.coordinator.finalize(files=harness.files, prompt="Adapt")

    assert identity.workspace_revision == workspace_revision_digest(harness.files)
    assert first.status is MaxFinalizationStatus.NEEDS_EDIT
    assert second.status is MaxFinalizationStatus.COMPLETE
    assert second.redacted_detail == "adaptation activation completed"
    assert attempts == [1, 2]
    operation = await db_session.get(Restoration, operation_id, populate_existing=True)
    run = await db_session.get(
        GenerationRun,
        harness.coordinator.generation_run_id,
        populate_existing=True,
    )
    assert operation is not None and run is not None
    assert operation.phase == "activation_offer_intent"
    assert operation.activation_request is not None
    assert "offer_request" in operation.activation_request
    assert operation.activation_request["candidate_files"] == harness.files
    assert run.agent_state["max_finalization"]["restoration_adaptation_proof"][
        "proof_digest"
    ] == f"{2:064x}"
    operation.activation_cancel_requested_at = datetime.now(UTC)
    operation.state = "reconciling"
    operation.phase = "activation_cancel"
    await db_session.commit()

    await harness.coordinator._persist_adaptation_proof(receipts[-1], harness.files)

    await db_session.refresh(operation)
    await db_session.refresh(run)
    assert operation.state == "reconciling"
    assert operation.phase == "activation_cancel"
    assert run.status == "running"


@pytest.mark.parametrize(
    ("fault", "terminal_status", "proof_state"),
    [
        pytest.param("sql_before_commit", None, "proof_ready", id="sql-commit-fault"),
        pytest.param("lost_proof_response", None, "proof_ready", id="lost-response"),
        pytest.param(
            "lost_proof_response",
            "failed",
            "proof_ready",
            id="failed-run-replays-issued-proof",
        ),
        pytest.param(
            "lost_proof_response",
            "failed",
            "migration_required",
            id="failed-run-settles-rejected-proof",
        ),
        pytest.param(
            "lost_proof_response",
            "cancelled",
            "proof_ready",
            id="cancelled-run-settles-proof-intent",
        ),
    ],
)
async def test_proof_request_is_replayed_without_releasing_candidate(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    terminal_status: str | None,
    proof_state: str,
) -> None:
    from omnia_api.services import restorations
    from omnia_api.services.generation_runs import (
        retry_terminal_adaptation_notifications,
        terminalize_generation_run_locked,
    )

    harness = await _new_harness(db_session, test_engine)
    operation_id = await _adaptation_run(db_session, harness)
    harness.files.update(
        {
            "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
            "src/app/api/visits/route.ts": _VISITS_ROUTE,
            "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
        }
    )
    identity = await harness.coordinator.executor.current_identity()  # type: ignore[misc]
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    proof_ready = proof_state == "proof_ready"
    receipt = RestorationAdaptationProof(
        state=proof_state,
        reason_code=None if proof_ready else "candidate_schema_changed",
        source_workspace_id=harness.coordinator.executor.restoration_adaptation_workspace.source_workspace_id,  # type: ignore[union-attr]
        candidate_workspace_id=identity.workspace_id,
        operation_id=operation_id,
        project_id=harness.coordinator.project_id,
        owner_id=run.user_id,
        generation_run_id=run.id,
        candidate_fencing_epoch=identity.fencing_epoch,
        proof_attempt=1,
        source_workspace_revision=identity.workspace_revision,
        candidate_workspace_revision=identity.workspace_revision,
        candidate_proof_key=identity.proof_key,
        candidate_artifact_digest=canonical_files_digest(harness.files),
        source_database_digest="1" * 64,
        candidate_database_digest="1" * 64,
        source_schema_digest="2" * 64,
        candidate_schema_digest="2" * 64,
        source_business_digest="3" * 64,
        candidate_business_digest="3" * 64,
        source_technical_digest="4" * 64,
        candidate_technical_digest="4" * 64,
        probe_contract_digest="5" * 64,
        probe_rehearsal_digest="6" * 64 if proof_ready else None,
        probe_rehearsal_database_digest="7" * 64 if proof_ready else None,
        candidate_source_manifest_digest="8" * 64,
        proof_digest="9" * 64,
        capabilities={
            "portable_machine": True,
            "database_admin": "isolated_copy",
            "restoration_adaptation_database_copy_v1": True,
            **({"restoration_adaptation_proof_v1": True} if proof_ready else {}),
        },
    )
    proof_calls: list[int] = []
    fail_next_commit = False
    original_commit = AsyncSession.commit

    async def first_proof(_identity, _artifact_digest, proof_attempt):
        nonlocal fail_next_commit
        proof_calls.append(proof_attempt)
        if fault == "lost_proof_response":
            raise TimeoutError("proof response lost after controller commit")
        fail_next_commit = True
        return receipt

    async def commit_with_fault(session):
        nonlocal fail_next_commit
        if fail_next_commit:
            fail_next_commit = False
            await session.rollback()
            raise RuntimeError("database disconnected before proof commit")
        await original_commit(session)

    harness.coordinator.executor = replace(
        harness.coordinator.executor,
        prove_restoration_adaptation=first_proof,
    )
    if fault == "sql_before_commit":
        monkeypatch.setattr(AsyncSession, "commit", commit_with_fault)
    with pytest.raises(AdaptationActivationRecoveryRequired):
        await harness.coordinator.finalize(files=harness.files, prompt="Adapt")

    await db_session.refresh(run)
    operation = await db_session.get(Restoration, operation_id, populate_existing=True)
    attempt = run.agent_state["max_finalization"]["restoration_adaptation_proof_attempt"]
    assert operation is not None
    assert attempt["status"] == "issued" and attempt["number"] == 1
    assert operation.state == "applying"
    assert operation.phase == "activation_proof_intent"
    assert "proof_request" in operation.activation_request
    assert await restorations.adaptation_activation_holds_generation_lease(
        harness.coordinator.session_factory, run.id
    )

    if terminal_status is not None:
        await terminalize_generation_run_locked(
            db_session,
            run,
            status=terminal_status,
            error=(
                "worker restarted before proof response"
                if terminal_status == "failed"
                else None
            ),
        )
        await db_session.commit()
        if terminal_status == "cancelled":
            assert await retry_terminal_adaptation_notifications(db_session) == 1
            await db_session.refresh(operation)
            await db_session.refresh(run)
            assert operation.state == "reconciling"
            assert operation.phase == "activation_cancel"
            assert run.agent_state["restoration_adaptation_owner_status"] == (
                "sealed_proof_retained"
            )

    async def replay(workspace, **kwargs):
        assert workspace == harness.coordinator.executor.restoration_adaptation_workspace
        assert kwargs["proof_attempt"] == 1
        assert kwargs["candidate_artifact_digest"] == receipt.candidate_artifact_digest
        proof_calls.append(kwargs["proof_attempt"])
        return receipt

    monkeypatch.setattr(restorations, "project_cell_prove_restoration_adaptation", replay)
    resumed = await restorations._resume_restoration_adaptation_activation(
        db_session,
        project_id=run.project_id,
        owner_id=run.user_id,
        operation_id=operation_id,
    )

    await db_session.refresh(run)
    await db_session.refresh(operation)
    if terminal_status == "cancelled":
        assert proof_calls == [1]
        assert resumed.state == "cancelled"
        assert operation.state == "cancelled"
        assert operation.phase == "activation_cancelled"
        assert operation.activation_request is None
        assert operation.activation_request_digest is None
        assert run.status == "cancelled"
        assert run.agent_state["restoration_adaptation_owner_status"] == (
            "terminal_pending"
        )
        assert not await restorations.adaptation_activation_holds_generation_lease(
            harness.coordinator.session_factory, run.id
        )
        return

    if terminal_status == "failed" and not proof_ready:
        assert proof_calls == [1, 1]
        assert resumed.state == "failed"
        assert operation.state == "failed"
        assert operation.phase == "generation"
        assert operation.activation_request is None
        assert operation.activation_request_digest is None
        assert run.status == "failed"
        assert run.agent_state["restoration_adaptation_owner_status"] == (
            "terminal_pending"
        )
        assert not await restorations.adaptation_activation_holds_generation_lease(
            harness.coordinator.session_factory, run.id
        )
        return

    assert proof_calls == [1, 1]
    assert resumed.state == "applying"
    assert operation.phase == "activation_offer_intent"
    assert operation.activation_request is not None
    assert "offer_request" in operation.activation_request
    assert run.agent_state["max_finalization"]["restoration_adaptation_proof"][
        "proof_digest"
    ] == receipt.proof_digest


async def test_ordinary_generation_is_not_capability_checked(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    harness = await _new_harness(db_session, test_engine)
    # No restoration_adaptation marker: removing routes is a normal edit.
    outcome = await harness.coordinator.finalize(files=_files(), prompt="Build tracker")
    assert outcome.status is MaxFinalizationStatus.COMPLETE
