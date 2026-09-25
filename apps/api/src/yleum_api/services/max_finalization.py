"""Single proof-carrying finalization owner for portable MAX generations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellProofResult,
)
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import repo
from yleum_api.services.agent_progress import bounded_redacted_text
from yleum_api.services.functional_gate import Check, FunctionalVerdict, summarize
from yleum_api.services.generation_deadline import (
    generation_deadline,
    is_restoration_adaptation,
    note_proof_sealed,
    note_proof_settled,
    note_repair_stage_started,
)
from yleum_api.services.generation_metrics import (
    GenerationPhase,
    increment_generation_counter,
    log_finalization_outcome,
    record_phase_finished,
    record_phase_started,
    record_terminal_reason,
)
from yleum_api.services.generation_runs import terminalize_generation_run_locked
from yleum_api.services.max_generation_contract import max_source_completion_gap
from yleum_api.services.max_runtime_probe import MaxRuntimeProbe
from yleum_api.services.orchestrator_client import RestorationAdaptationProof
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityStart,
    ActivityState,
    ProjectCellActivityConflict,
    finish_activity,
    heartbeat_activity,
    run_with_activity_lease,
    start_activity,
)
from yleum_api.services.project_cell_candidates import prepare_candidate, promote_candidate
from yleum_api.services.project_cell_errors import (
    PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED,
    ProjectCellInfrastructureError,
)
from yleum_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
    ProjectCellExecutorHandle,
)
from yleum_api.services.project_cell_proofs import (
    ProjectCellProofConflict,
    ProofDimension,
    ProofIdentity,
    ProofOutcome,
    create_proof_identity,
    find_proof_result,
    record_proof_result,
)
from yleum_api.services.promotion_permit import (
    MAX_FULL_BUILD_CONTRACT_VERSION,
    PromotionPermit,
    PromotionPermitError,
    canonical_files_digest,
    issue_promotion_permit,
    release_receipt_digest,
    release_receipt_matches,
    release_receipt_ref,
    require_promotion_permit,
    workspace_revision_digest,
)
from yleum_api.services.restoration_adaptation import restoration_probe_source_gap
from yleum_api.services.versioning_capabilities import capability_gap

_MAX_DETAIL_BYTES = 4096
# A repair is one model turn plus a build; starting it with less time only burns the rest.
_MIN_REPAIR_SECONDS = 120
# Сколько раз подряд источнику возвращают замечания. У обычной правки три прохода:
# больше почти всегда означает, что агент ходит по кругу.
_ORDINARY_REPAIR_ROUNDS = 3
# У адаптации правил больше, и агент узнаёт их по одному, каждое — отдельный круг с
# полной пересборкой. Прогон c32cdde8 (25.09) закончился ровно на том, что второе
# правило ему назвали и тут же остановили, истратив при этом половину бюджета
# времени. Настоящая граница у адаптации — окно починки по времени; счётчик поверх
# него не защищает ни от чего, потому что агент, ничего не изменивший, и так
# останавливается сразу.
_ADAPTATION_REPAIR_ROUNDS = 6
# How often the watchdog looks again while a sealed hand-off runs under its own ceiling.
_SEALED_RECHECK_SECONDS = 15.0
_FULL_BUILD_DETAIL_PREFIX = f"[build-contract:{MAX_FULL_BUILD_CONTRACT_VERSION}]"


async def _discard_event(
    _event_type: str,
    _payload: Mapping[str, object],
) -> None:
    return None


class MaxFinalizationConflict(RuntimeError):
    """The durable checkpoint no longer belongs to the active fenced identity."""


class AdaptationBaselineUnavailable(RuntimeError):
    """A recognised adaptation lost the draft it must be compared with (AV06.1).

    Without that baseline no capability can be proven preserved, so the run
    fails instead of passing on "no differences found"."""


class AdaptationActivationRecoveryRequired(RuntimeError):
    """A sealed adaptation proof owns the candidate until forward recovery."""


class MaxFinalizationStatus(StrEnum):
    NEEDS_EDIT = "needs_edit"
    ACTIVATING = "activating"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class MaxFinalizationCheckpoint:
    generation_run_id: UUID
    workspace_id: UUID
    proof_key: str
    phase: GenerationPhase
    operation_id: UUID | None
    candidate_id: UUID | None
    acceptance_id: str

    def to_json(self) -> dict[str, object]:
        return {
            "generation_run_id": str(self.generation_run_id),
            "workspace_id": str(self.workspace_id),
            "proof_key": self.proof_key,
            "phase": self.phase.value,
            "operation_id": str(self.operation_id) if self.operation_id else None,
            "candidate_id": str(self.candidate_id) if self.candidate_id else None,
            "acceptance_id": self.acceptance_id,
        }


@dataclass(frozen=True, slots=True)
class ProofBundle:
    identity: ProjectCellProof
    bootstrap: ProjectCellProofResult | None = None
    fast_check: ProjectCellProofResult | None = None
    full_build: ProjectCellProofResult | None = None
    runtime: ProjectCellProofResult | None = None
    release: ProjectCellProofResult | None = None
    permit: PromotionPermit | None = None

    def release_checks(self, *, require_max_data: bool) -> list[Check]:
        checks = [
            _result_check("typecheck", self.full_build),
            _result_check("runtime", self.runtime),
        ]
        if self.release is not None:
            checks.append(_result_check("release", self.release))
        return checks


@dataclass(frozen=True, slots=True)
class MaxFinalizationOutcome:
    status: MaxFinalizationStatus
    checkpoint: MaxFinalizationCheckpoint
    proof: ProofBundle
    redacted_detail: str


@dataclass(frozen=True, slots=True)
class _LocalActivityStatus:
    state: str
    phase: str
    heartbeat_at: datetime
    log_bytes: int = 0
    terminal_response: None = None


def _result_check(name: str, result: ProjectCellProofResult | None) -> Check:
    return Check(
        name,
        result is not None and result.outcome == ProofOutcome.GREEN.value,
        result.redacted_detail[:240] if result is not None else "proof missing",
    )


def _artifact_digest(result: ProjectCellProofResult) -> str:
    ref = result.artifact_ref or ""
    digest = ref.rsplit("/", 1)[-1]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise MaxFinalizationConflict("proof artifact is not content-addressed")
    return digest


def _content_digest(label: str, *parts: str) -> str:
    payload = "\0".join((label, *parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _versioned_build_detail(detail: str) -> str:
    return _FULL_BUILD_DETAIL_PREFIX + ("\n" + detail if detail else "")


def _current_build_result(result: ProjectCellProofResult, artifact_digest: str) -> bool:
    if not result.redacted_detail.startswith(_FULL_BUILD_DETAIL_PREFIX):
        return False
    if result.outcome == ProofOutcome.RED.value:
        return True
    return result.artifact_ref == f"build/sha256/{artifact_digest}"


def _adaptation_proof_capability_gap(
    bundle: object,
    capabilities: Mapping[str, object],
) -> str | None:
    from yleum_api.services.restoration_adaptation import has_current_adaptation_contract

    if not has_current_adaptation_contract(bundle):
        return "adaptation_proof_unavailable: immutable preservation contract"
    if capabilities.get("restoration_adaptation_database_copy_v1") is not True:
        return "adaptation_proof_unavailable: isolated database copy"
    if capabilities.get("restoration_adaptation_proof_v1") is not True:
        return "adaptation_proof_unavailable: schema, CRUD, reload and owner isolation"
    return None


class MaxFinalizationCoordinator:
    """Own every deterministic step after the edit loop for one generation."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        generation_run_id: UUID,
        project_id: UUID,
        project_slug: str,
        executor: ProjectCellExecutorHandle,
        emit: Callable[[str, Mapping[str, object]], Awaitable[None]] | None = None,
    ) -> None:
        if executor.current_identity is None or executor.run_role is None:
            raise ValueError("identity-aware Project Cell executor is required")
        if executor.runtime_probe is None:
            raise ValueError("Project Cell runtime proof is required")
        if executor.operation_status is None:
            raise ValueError("Project Cell operation status is required")
        self.session_factory = session_factory
        self.generation_run_id = generation_run_id
        self.project_id = project_id
        self.project_slug = project_slug
        self.executor = executor
        self.emit = emit or _discard_event
        self._last_files: dict[str, str] | None = None
        self._last_prompt: str | None = None

    async def _adaptation_capability_gap(self, files: Mapping[str, str]) -> str | None:
        """AV06: an adaptation run must keep every route the draft had before it.

        Ordinary generations (no adaptation bundle) are never gated. A recognised
        adaptation whose baseline is missing, foreign or unreadable raises
        :class:`AdaptationBaselineUnavailable` (FV030/FV031)."""
        async with self.session_factory() as session:
            run = await session.get(GenerationRun, self.generation_run_id)
            state = run.agent_state if run is not None else None
            bundle = state.get("restoration_adaptation") if isinstance(state, dict) else None
            if not isinstance(bundle, dict):
                return None
            proof_gap = _adaptation_proof_capability_gap(bundle, self.executor.capabilities)
            if (
                proof_gap
                == "adaptation_proof_unavailable: schema, CRUD, reload and owner isolation"
                and self.executor.prove_restoration_adaptation is not None
            ):
                # The immutable preservation proof is produced only after the
                # candidate build/runtime/release evidence is frozen.
                proof_gap = None
            if proof_gap is not None:
                raise AdaptationBaselineUnavailable(proof_gap)
            raw_id = bundle.get("base_draft_snapshot_id")
            try:
                snapshot_id = UUID(str(raw_id))
            except (TypeError, ValueError) as exc:
                raise AdaptationBaselineUnavailable(
                    "baseline_unavailable: adaptation has no base draft snapshot"
                ) from exc
            snapshot = await session.get(Snapshot, snapshot_id)
        if snapshot is None:
            raise AdaptationBaselineUnavailable("baseline_unavailable: base draft snapshot is gone")
        if snapshot.project_id != self.project_id:
            raise AdaptationBaselineUnavailable(
                "baseline_unavailable: base draft snapshot belongs to another project"
            )
        try:
            before = await asyncio.to_thread(repo.read_files, self.project_id, snapshot.commit_sha)
        except Exception as exc:
            raise AdaptationBaselineUnavailable(
                "baseline_unavailable: base draft source cannot be read"
            ) from exc
        if not before:
            raise AdaptationBaselineUnavailable("baseline_unavailable: base draft source is empty")
        return capability_gap(before, files)

    async def _raise_persisted_infrastructure_failure(self) -> None:
        # A restart or edited source cannot make a protected controller failure
        # repairable by the model. Only a new generation after operator recovery
        # can attempt this infrastructure again.
        async with self.session_factory() as session:
            operation_id = await session.scalar(
                select(ProjectCellActivityLease.operation_id)
                .where(
                    ProjectCellActivityLease.workspace_id == self.executor.workspace_id,
                    ProjectCellActivityLease.generation_run_id == self.generation_run_id,
                    ProjectCellActivityLease.state == ActivityState.FAILED.value,
                    ProjectCellActivityLease.redacted_diagnostic
                    == PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED,
                )
                .order_by(ProjectCellActivityLease.started_at)
                .limit(1)
            )
        if operation_id is not None:
            raise ProjectCellInfrastructureError(
                PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED, operation_id
            )

    async def fast_check(self) -> ProjectCellProofResult:
        await self._raise_persisted_infrastructure_failure()
        if self.executor.prove_restoration_adaptation is not None:
            return await self._adaptation_fast_check()
        identity = await self._identity()
        proof = await self._proof(identity)
        bootstrap = await self._find(proof, ProofDimension.BOOTSTRAP)
        if bootstrap is None:
            bootstrap, identity, proof = await self._run_bootstrap(proof, identity)
        else:
            await self._counter("proof_hit")
        if bootstrap.outcome != ProofOutcome.GREEN.value:
            return bootstrap
        result = await self._find(proof, ProofDimension.FAST_CHECK)
        if result is not None:
            await self._counter("proof_hit")
            return result
        return await self._run_role_result(
            proof=proof,
            identity=identity,
            dimension=ProofDimension.FAST_CHECK,
            role=ProjectCellCommandRole.FAST_CHECK,
            phase=GenerationPhase.FAST_CHECK,
        )

    async def finalize(
        self,
        *,
        files: Mapping[str, str],
        prompt: str,
    ) -> MaxFinalizationOutcome:
        await self._raise_persisted_infrastructure_failure()
        if self.executor.prove_restoration_adaptation is not None:
            return await self._finalize_adaptation(files=files, prompt=prompt)
        self._last_files = dict(files)
        self._last_prompt = prompt
        identity = await self._identity()
        if workspace_revision_digest(files) != identity.workspace_revision:
            raise MaxFinalizationConflict("source files do not match the active workspace revision")
        proof = await self._proof(identity)
        checkpoint = self._checkpoint(identity, GenerationPhase.PREPARE)
        source_gap = max_source_completion_gap(prompt, files, portable=True)
        if source_gap is None:
            try:
                source_gap = await self._adaptation_capability_gap(files)
            except AdaptationBaselineUnavailable as exc:
                # Not repairable by the model: no baseline, no proof, no promotion.
                outcome = await self._outcome(
                    MaxFinalizationStatus.FAILED,
                    self._checkpoint(identity, GenerationPhase.EDIT),
                    ProofBundle(identity=proof),
                    str(exc),
                )
                await self._log_terminal(outcome)
                return outcome
        if source_gap is not None:
            return await self._outcome(
                MaxFinalizationStatus.NEEDS_EDIT,
                self._checkpoint(identity, GenerationPhase.EDIT),
                ProofBundle(identity=proof),
                source_gap,
            )

        await self._phase_started(GenerationPhase.PREPARE, checkpoint)
        bootstrap = await self._find(proof, ProofDimension.BOOTSTRAP)
        if bootstrap is None:
            bootstrap, identity, proof = await self._run_bootstrap(proof, identity)
        else:
            await self._counter("proof_hit")
        if bootstrap.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.PREPARE,
                bootstrap,
                bootstrap.redacted_detail,
            )
        await self._phase_finished(GenerationPhase.PREPARE)

        build = await self._find(proof, ProofDimension.FULL_BUILD)
        refresh_dependents = False
        if build is not None:
            current_files = await self._verified_workspace_files(identity)
            current_digest = canonical_files_digest(current_files)
            if not _current_build_result(build, current_digest):
                build = None
                refresh_dependents = True
        if build is None:
            build = await self._run_role_result(
                proof=proof,
                identity=identity,
                dimension=ProofDimension.FULL_BUILD,
                role=ProjectCellCommandRole.FULL_BUILD,
                phase=GenerationPhase.FINAL_BUILD,
            )
        else:
            await self._counter("proof_hit")
        bundle = ProofBundle(identity=proof, bootstrap=bootstrap, full_build=build)
        if build.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.FINAL_BUILD,
                build,
                build.redacted_detail,
                bundle=bundle,
            )

        build_digest = _artifact_digest(build)
        runtime = None
        if not refresh_dependents:
            runtime = await self._find(
                proof,
                ProofDimension.RUNTIME,
                artifact_digest=build_digest,
            )
        if runtime is None:
            runtime = await self._run_runtime(
                proof,
                identity,
                build_digest,
                refresh_incompatible=refresh_dependents,
            )
        else:
            await self._counter("proof_hit")
        bundle = ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
        )
        if runtime.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.RUNTIME_PROBE,
                runtime,
                runtime.redacted_detail,
                bundle=bundle,
            )

        release = None
        if not refresh_dependents:
            release = await self._find(
                proof,
                ProofDimension.RELEASE,
                artifact_digest=build_digest,
            )
        if release is not None and not release_receipt_matches(
            release,
            build_digest,
            proof.proof_key,
        ):
            release = None
        if release is None:
            release = await self._record_release(bundle, build_digest)
        else:
            await self._counter("proof_hit")
        bundle = ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
            release=release,
        )
        if release.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.RUNTIME_PROBE,
                release,
                release.redacted_detail,
                bundle=bundle,
            )

        final_files = await self._verified_workspace_files(identity)
        if canonical_files_digest(final_files) != build_digest:
            raise MaxFinalizationConflict("built artifact changed after release proof")
        self._last_files = final_files
        permit = issue_promotion_permit(bundle)
        bundle = ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
            release=release,
            permit=permit,
        )
        candidate = await self._prepare_and_promote(identity, build, release, permit)
        complete = self._checkpoint(
            identity,
            GenerationPhase.COMPLETE,
            candidate_id=candidate.id,
        )
        await self._phase_started(GenerationPhase.COMPLETE, complete)
        await self._phase_finished(GenerationPhase.COMPLETE)
        outcome = await self._outcome(
            MaxFinalizationStatus.COMPLETE,
            complete,
            bundle,
            "final proof accepted",
        )
        await self._log_terminal(outcome)
        return outcome

    async def finalize_with_repair(
        self,
        *,
        prompt: str,
        repair: Callable[[str], Awaitable[None]],
    ) -> MaxFinalizationOutcome:
        """Return source feedback to the same editor a bounded number of times.

        Only NEEDS_EDIT is repairable here. Infrastructure/proof failures and
        cancellation stay terminal. Every pass observes the actual workspace;
        unchanged source cannot earn another build or an infinite model loop.

        Обычной правке хватает трёх проходов. Адаптация узнаёт требования к
        проверочной точке по одному правилу за круг, поэтому проходов у неё
        больше; ограничивает её при этом окно починки по времени, а не счётчик.
        """
        await self._raise_persisted_infrastructure_failure()
        async with self.session_factory() as session:
            run = await self._locked_run(session)
            rounds = (
                _ADAPTATION_REPAIR_ROUNDS
                if is_restoration_adaptation(run)
                else _ORDINARY_REPAIR_ROUNDS
            )
            # The agent's own turn is over: an adaptation's checks and repairs no
            # longer compete with it for one limit.
            note_repair_stage_started(run)
            await session.commit()
        files = await self.executor.snapshot_files()
        for attempt in range(rounds):
            outcome = await self.finalize(files=files, prompt=prompt)
            if outcome.status is not MaxFinalizationStatus.NEEDS_EDIT or attempt == rounds - 1:
                return outcome
            async with self.session_factory() as session:
                run = await self._locked_run(session)
                if run.status == "cancel_requested":
                    raise asyncio.CancelledError
                deadline = generation_deadline(run).at
            # NEEDS_EDIT means the last proof, if any, was settled; a run still holding a
            # durable intent has no business starting another editing pass.
            if deadline is None:
                raise MaxFinalizationConflict(
                    "sealed restoration adaptation cannot enter a source repair"
                )
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining < _MIN_REPAIR_SECONDS:
                raise TimeoutError("generation deadline exceeded before source repair")
            try:
                async with asyncio.timeout(remaining):
                    await repair(outcome.redacted_detail)
            except TimeoutError as exc:
                # A bare TimeoutError has no text; the run would fail with an empty reason.
                raise TimeoutError("generation deadline exceeded during source repair") from exc
            updated = await self.executor.snapshot_files()
            if updated == files:
                return outcome
            files = updated
        raise AssertionError("bounded finalization loop exhausted")

    @staticmethod
    def _transient_proof(identity: ProofIdentity) -> ProjectCellProof:
        return ProjectCellProof(
            id=uuid5(
                identity.generation_run_id,
                f"adaptation-proof:{identity.workspace_id}:{identity.proof_key}",
            ),
            workspace_id=identity.workspace_id,
            generation_run_id=identity.generation_run_id,
            fencing_epoch=identity.fencing_epoch,
            proof_key=identity.proof_key,
            workspace_revision=identity.workspace_revision,
            dependency_digest=identity.dependency_digest,
            schema_data_digest=identity.schema_data_digest,
            cell_manifest_digest=identity.cell_manifest_digest,
            base_image_digest=identity.base_image_digest,
            toolchain_digest=identity.toolchain_digest,
            resource_profile_version=identity.resource_profile_version,
            build_config_digest=identity.build_config_digest,
        )

    @staticmethod
    def _transient_result(
        *,
        identity: ProofIdentity,
        proof: ProjectCellProof,
        dimension: ProofDimension,
        outcome: ProofOutcome,
        operation_id: UUID,
        detail: str,
        artifact_ref: str | None = None,
        artifact_digest: str | None = None,
    ) -> ProjectCellProofResult:
        detail_text = bounded_redacted_text(detail, max_bytes=_MAX_DETAIL_BYTES)
        return ProjectCellProofResult(
            id=uuid5(
                operation_id,
                f"adaptation-result:{dimension.value}:{identity.proof_key}",
            ),
            proof_id=proof.id,
            workspace_id=identity.workspace_id,
            dimension=dimension.value,
            dimension_key=identity.dimension_key(
                dimension,
                artifact_digest=(
                    artifact_digest
                    if dimension in {ProofDimension.RUNTIME, ProofDimension.RELEASE}
                    else None
                ),
            ),
            outcome=outcome.value,
            operation_id=operation_id,
            artifact_ref=artifact_ref,
            detail_digest=hashlib.sha256(detail_text.encode("utf-8")).hexdigest(),
            redacted_detail=detail_text,
        )

    async def _adaptation_role_result(
        self,
        *,
        identity: ProofIdentity,
        proof: ProjectCellProof,
        dimension: ProofDimension,
        role: ProjectCellCommandRole,
    ) -> tuple[ProjectCellProofResult, ProofIdentity, ProjectCellProof]:
        run_role = self.executor.run_role
        assert run_role is not None
        operation_id = uuid5(
            self.generation_run_id,
            "adaptation-command:"
            f"{identity.workspace_id}:{identity.fencing_epoch}:{identity.proof_key}:"
            f"{role.value}:{MAX_FULL_BUILD_CONTRACT_VERSION}",
        )
        observation = await run_role(role, operation_id)
        if observation.before != identity:
            raise MaxFinalizationConflict("candidate command started from another proof identity")
        final_identity = observation.after
        final_proof = (
            proof
            if final_identity.proof_key == identity.proof_key
            else self._transient_proof(final_identity)
        )
        changed_frozen_identity = (
            dimension is not ProofDimension.BOOTSTRAP and final_identity != identity
        )
        outcome = (
            ProofOutcome.GREEN
            if observation.ok and not changed_frozen_identity
            else ProofOutcome.RED
        )
        detail = observation.redacted_detail
        if changed_frozen_identity:
            detail = "command changed the frozen candidate proof identity" + (
                "\n" + detail if detail else ""
            )
        artifact_ref = None
        artifact_digest = None
        if dimension is ProofDimension.FULL_BUILD:
            detail = _versioned_build_detail(detail)
            if outcome is ProofOutcome.GREEN:
                artifact_files = await self._verified_workspace_files(identity)
                artifact_digest = canonical_files_digest(artifact_files)
                artifact_ref = f"build/sha256/{artifact_digest}"
                self._last_files = artifact_files
        result = self._transient_result(
            identity=final_identity if dimension is ProofDimension.BOOTSTRAP else identity,
            proof=final_proof if dimension is ProofDimension.BOOTSTRAP else proof,
            dimension=dimension,
            outcome=outcome,
            operation_id=operation_id,
            detail=detail,
            artifact_ref=artifact_ref,
            artifact_digest=artifact_digest,
        )
        return result, final_identity, final_proof

    async def _adaptation_fast_check(self) -> ProjectCellProofResult:
        identity = await self._identity()
        proof = self._transient_proof(identity)
        bootstrap, identity, proof = await self._adaptation_role_result(
            identity=identity,
            proof=proof,
            dimension=ProofDimension.BOOTSTRAP,
            role=ProjectCellCommandRole.BOOTSTRAP,
        )
        if bootstrap.outcome != ProofOutcome.GREEN.value:
            return bootstrap
        result, _identity, _proof = await self._adaptation_role_result(
            identity=identity,
            proof=proof,
            dimension=ProofDimension.FAST_CHECK,
            role=ProjectCellCommandRole.FAST_CHECK,
        )
        return result

    async def _adaptation_runtime_result(
        self,
        *,
        identity: ProofIdentity,
        proof: ProjectCellProof,
        build_digest: str,
    ) -> ProjectCellProofResult:
        runtime_probe = self.executor.runtime_probe
        assert runtime_probe is not None
        operation_id = uuid5(
            self.generation_run_id,
            f"adaptation-runtime:{identity.proof_key}:{build_digest}",
        )
        probe = cast(MaxRuntimeProbe, await runtime_probe(identity.proof_key))
        current = await self._identity()
        unchanged = current == identity
        ok = bool(probe.ok) and unchanged
        detail = str(probe.detail)
        if not unchanged:
            detail = "runtime probe changed the frozen candidate proof identity" + (
                "\n" + detail if detail else ""
            )
        verification_digest = _content_digest(
            "runtime",
            identity.proof_key,
            build_digest,
            str(getattr(probe, "artifact_digest", "")),
        )
        return self._transient_result(
            identity=identity,
            proof=proof,
            dimension=ProofDimension.RUNTIME,
            outcome=ProofOutcome.GREEN if ok else ProofOutcome.RED,
            operation_id=operation_id,
            artifact_ref=f"verification/sha256/{verification_digest}" if ok else None,
            detail=detail,
            artifact_digest=build_digest,
        )

    async def _adaptation_release_result(
        self,
        *,
        identity: ProofIdentity,
        bundle: ProofBundle,
        build_digest: str,
    ) -> ProjectCellProofResult:
        from yleum_api.services.release_proof import run_release_proof

        operation_id = uuid5(
            self.generation_run_id,
            f"adaptation-release:{identity.proof_key}:{build_digest}",
        )
        verdict = await run_release_proof(
            self.project_id,
            self.project_slug,
            proof=bundle,
            require_max_data=True,
            project_cell_handle=self.executor,
        )
        current = await self._identity()
        unchanged = current == identity
        detail = bounded_redacted_text(verdict.summary, max_bytes=_MAX_DETAIL_BYTES)
        if not unchanged:
            detail = bounded_redacted_text(
                "release proof changed the frozen candidate proof identity\n" + detail,
                max_bytes=_MAX_DETAIL_BYTES,
            )
        digest = release_receipt_digest(
            proof_key=identity.proof_key,
            artifact_digest=build_digest,
            detail=detail,
        )
        return self._transient_result(
            identity=identity,
            proof=bundle.identity,
            dimension=ProofDimension.RELEASE,
            outcome=(
                ProofOutcome.GREEN if verdict.passed and unchanged else ProofOutcome.RED
            ),
            operation_id=operation_id,
            artifact_ref=release_receipt_ref(
                artifact_digest=build_digest,
                receipt_digest=digest,
            ),
            detail=detail,
            artifact_digest=build_digest,
        )

    async def _issue_adaptation_proof_attempt(
        self,
        *,
        identity: ProofIdentity,
        artifact_digest: str,
        files: Mapping[str, str],
    ) -> int:
        from yleum_api.services.restorations import _owned_operation, _touch

        workspace = self.executor.restoration_adaptation_workspace
        if workspace is None:
            raise MaxFinalizationConflict("restoration adaptation workspace is missing")
        exact_files = dict(files)
        if canonical_files_digest(exact_files) != artifact_digest:
            raise MaxFinalizationConflict("restoration adaptation artifact changed")
        binding = {
            "candidate_workspace_id": str(identity.workspace_id),
            "candidate_fencing_epoch": identity.fencing_epoch,
            "candidate_workspace_revision": identity.workspace_revision,
            "candidate_proof_key": identity.proof_key,
            "candidate_artifact_digest": artifact_digest,
        }
        async with self.session_factory() as session:
            run_hint = await session.get(GenerationRun, self.generation_run_id)
            hint_state = (
                run_hint.agent_state
                if run_hint is not None and isinstance(run_hint.agent_state, dict)
                else {}
            )
            raw_adaptation = hint_state.get("restoration_adaptation")
            try:
                operation_id = UUID(str(cast(dict[str, object], raw_adaptation)["operation_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise MaxFinalizationConflict(
                    "restoration adaptation binding is invalid"
                ) from exc
            _project, operation = await _owned_operation(
                session,
                self.project_id,
                workspace.owner_id,
                operation_id,
            )
            run = await session.get(
                GenerationRun, self.generation_run_id, with_for_update=True
            )
            if (
                run is None
                or run.project_id != self.project_id
                or run.user_id != workspace.owner_id
                or run.status not in {"running", "cancel_requested"}
                or operation.selected_branch != "adaptive"
                or operation.adaptation_run_id != run.id
                or operation.workspace_id != workspace.source_workspace_id
                or workspace.operation_id != operation.id
                or workspace.project_id != run.project_id
                or workspace.generation_run_id != run.id
                or workspace.candidate_workspace_id != identity.workspace_id
                or workspace.candidate_fencing_epoch != identity.fencing_epoch
            ):
                raise MaxFinalizationConflict(
                    "restoration adaptation proof ownership changed"
                )
            root = dict(run.agent_state)
            raw_state = root.get("max_finalization")
            state = dict(raw_state) if isinstance(raw_state, dict) else {}
            raw_attempt = state.get("restoration_adaptation_proof_attempt")
            current = dict(raw_attempt) if isinstance(raw_attempt, dict) else None
            if current is not None and current.get("status") == "issued":
                if any(current.get(key) != value for key, value in binding.items()):
                    raise MaxFinalizationConflict(
                        "restoration adaptation proof attempt identity changed"
                    )
                number = current.get("number")
                if type(number) is not int or number < 1:
                    raise MaxFinalizationConflict(
                        "restoration adaptation proof attempt is invalid"
                    )
            elif current is not None:
                prior = state.get("restoration_adaptation_proof")
                if not isinstance(prior, dict) or prior.get("state") != "migration_required":
                    raise MaxFinalizationConflict(
                        "restoration adaptation proof is already terminal"
                    )
                previous = current.get("number")
                if type(previous) is not int or previous < 1:
                    raise MaxFinalizationConflict(
                        "restoration adaptation proof attempt is invalid"
                    )
                number = previous + 1
            else:
                number = 1
            proof_request = {
                "workspace": {
                    "source_workspace_id": str(workspace.source_workspace_id),
                    "candidate_workspace_id": str(workspace.candidate_workspace_id),
                    "operation_id": str(workspace.operation_id),
                    "project_id": str(workspace.project_id),
                    "owner_id": str(workspace.owner_id),
                    "generation_run_id": str(workspace.generation_run_id),
                    "candidate_fencing_epoch": workspace.candidate_fencing_epoch,
                    "source_database_digest": workspace.source_database_digest,
                    "proof_digest": workspace.proof_digest,
                    "capabilities": dict(workspace.capabilities),
                },
                **binding,
                "proof_attempt": number,
            }
            intent = {
                "proof_request": proof_request,
                "candidate_files": exact_files,
                "candidate_artifact_digest": artifact_digest,
            }
            if operation.activation_request is not None and operation.activation_request != intent:
                raise MaxFinalizationConflict(
                    "restoration adaptation proof intent changed"
                )
            state["restoration_adaptation_proof_attempt"] = {
                "number": number,
                "status": "issued",
                **binding,
            }
            root["max_finalization"] = state
            run.agent_state = root
            note_proof_sealed(run)
            operation.activation_request = intent
            operation.activation_request_digest = hashlib.sha256(
                json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            operation.state = "applying"
            operation.phase = "activation_proof_intent"
            operation.error = None
            _touch(operation)
            await session.commit()
            return number

    async def _persist_adaptation_proof(
        self,
        receipt: RestorationAdaptationProof,
        files: Mapping[str, str],
    ) -> None:
        async with self.session_factory() as session:
            await self.persist_adaptation_proof_receipt(
                session,
                generation_run_id=self.generation_run_id,
                project_id=self.project_id,
                receipt=receipt,
                files=files,
            )

    @staticmethod
    async def persist_adaptation_proof_receipt(
        session: AsyncSession,
        *,
        generation_run_id: UUID,
        project_id: UUID,
        receipt: RestorationAdaptationProof,
        files: Mapping[str, str],
    ) -> None:
        from yleum_api.services.restorations import (
            _activation_offer_request,
            _adaptation_cancel_requested,
            _owned_operation,
            _touch,
        )

        run_hint = await session.get(GenerationRun, generation_run_id)
        if run_hint is None or run_hint.project_id != project_id:
            raise MaxFinalizationConflict("generation run not found for project")
        hint_state = run_hint.agent_state if isinstance(run_hint.agent_state, dict) else {}
        raw_binding = hint_state.get("restoration_adaptation")
        if not isinstance(raw_binding, dict):
            raise MaxFinalizationConflict("restoration adaptation binding is missing")
        try:
            operation_id = UUID(str(raw_binding["operation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise MaxFinalizationConflict(
                "restoration adaptation binding is invalid"
            ) from exc
        _project, operation = await _owned_operation(
            session,
            run_hint.project_id,
            run_hint.user_id,
            operation_id,
        )
        run = await session.get(GenerationRun, generation_run_id, with_for_update=True)
        if run is None or run.project_id != project_id:
            raise MaxFinalizationConflict("generation run not found for project")
        durable_cancel = bool(
            not operation.activation_effects_admitted
            and (
                operation.activation_cancel_requested_at is not None
                or operation.phase in {"activation_cancel", "activation_cancelled"}
                or _adaptation_cancel_requested(run)
            )
        )
        root = dict(run.agent_state) if isinstance(run.agent_state, dict) else {}
        raw_state = root.get("max_finalization")
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        raw_attempt = state.get("restoration_adaptation_proof_attempt")
        attempt = dict(raw_attempt) if isinstance(raw_attempt, dict) else None
        issued_failed_recovery = bool(
            run.status == "failed"
            and root.get("restoration_adaptation_owner_status")
            == "sealed_proof_retained"
            and operation.selected_branch == "adaptive"
            and operation.adaptation_run_id == run.id
            and operation.state in {"applying", "reconciling"}
            and operation.phase == "activation_proof_intent"
            and isinstance(operation.activation_request, dict)
            and "proof_request" in operation.activation_request
            and attempt is not None
            and attempt.get("status") == "issued"
        )
        if (
            run.status not in {"running", "cancel_requested"}
            and not (run.status == "cancelled" and durable_cancel)
            and not issued_failed_recovery
        ):
            raise MaxFinalizationConflict("generation run is not active")
        if (
            receipt.operation_id != operation.id
            or receipt.project_id != run.project_id
            or receipt.owner_id != run.user_id
            or receipt.generation_run_id != run.id
            or receipt.source_workspace_id != operation.workspace_id
        ):
            raise MaxFinalizationConflict(
                "restoration adaptation proof ownership changed"
            )
        if (
            attempt is None
            or attempt.get("number") != receipt.proof_attempt
            or attempt.get("candidate_workspace_id")
            != str(receipt.candidate_workspace_id)
            or attempt.get("candidate_fencing_epoch")
            != receipt.candidate_fencing_epoch
            or attempt.get("candidate_workspace_revision")
            != receipt.candidate_workspace_revision
            or attempt.get("candidate_proof_key") != receipt.candidate_proof_key
            or attempt.get("candidate_artifact_digest")
            != receipt.candidate_artifact_digest
        ):
            raise MaxFinalizationConflict(
                "restoration adaptation proof receipt changed"
            )
        if attempt.get("status") == "completed":
            prior = state.get("restoration_adaptation_proof")
            if not (
                isinstance(prior, dict)
                and prior.get("proof_digest") == receipt.proof_digest
            ):
                raise MaxFinalizationConflict(
                    "restoration adaptation proof attempt was already completed"
                )
        elif attempt.get("status") != "issued":
            raise MaxFinalizationConflict(
                "restoration adaptation proof attempt is not issued"
            )
        if attempt.get("status") == "issued":
            proof_intent = operation.activation_request
            proof_request = (
                proof_intent.get("proof_request")
                if isinstance(proof_intent, dict)
                else None
            )
            if (
                not isinstance(proof_intent, dict)
                or not isinstance(proof_request, dict)
                or proof_intent.get("candidate_files") != dict(files)
                or proof_intent.get("candidate_artifact_digest")
                != receipt.candidate_artifact_digest
                or proof_request.get("proof_attempt") != receipt.proof_attempt
                or proof_request.get("candidate_workspace_id")
                != str(receipt.candidate_workspace_id)
                or proof_request.get("candidate_fencing_epoch")
                != receipt.candidate_fencing_epoch
                or proof_request.get("candidate_workspace_revision")
                != receipt.candidate_workspace_revision
                or proof_request.get("candidate_proof_key")
                != receipt.candidate_proof_key
                or proof_request.get("candidate_artifact_digest")
                != receipt.candidate_artifact_digest
            ):
                raise MaxFinalizationConflict(
                    "restoration adaptation proof intent changed"
                )
        state["restoration_adaptation_proof"] = {
            "state": receipt.state,
            "reason_code": receipt.reason_code,
            "proof_digest": receipt.proof_digest,
            "proof_attempt": receipt.proof_attempt,
            "operation_id": str(receipt.operation_id),
            "source_workspace_id": str(receipt.source_workspace_id),
            "candidate_workspace_id": str(receipt.candidate_workspace_id),
            "candidate_fencing_epoch": receipt.candidate_fencing_epoch,
            "candidate_proof_key": receipt.candidate_proof_key,
            "candidate_artifact_digest": receipt.candidate_artifact_digest,
            "source_workspace_revision": receipt.source_workspace_revision,
            "candidate_workspace_revision": receipt.candidate_workspace_revision,
            "source_database_digest": receipt.source_database_digest,
            "candidate_database_digest": receipt.candidate_database_digest,
            "source_schema_digest": receipt.source_schema_digest,
            "candidate_schema_digest": receipt.candidate_schema_digest,
            "source_business_digest": receipt.source_business_digest,
            "candidate_business_digest": receipt.candidate_business_digest,
            "source_technical_digest": receipt.source_technical_digest,
            "candidate_technical_digest": receipt.candidate_technical_digest,
            "probe_contract_digest": receipt.probe_contract_digest,
            "probe_rehearsal_digest": receipt.probe_rehearsal_digest,
            "probe_rehearsal_database_digest": receipt.probe_rehearsal_database_digest,
            "candidate_source_manifest_digest": receipt.candidate_source_manifest_digest,
            "capabilities": dict(receipt.capabilities),
        }
        state["restoration_adaptation_proof_attempt"] = {
            **attempt,
            "status": "completed",
            "proof_digest": receipt.proof_digest,
        }
        root["max_finalization"] = state
        run.agent_state = root
        has_proof_intent = bool(
            isinstance(operation.activation_request, dict)
            and "proof_request" in operation.activation_request
        )
        if receipt.state == "proof_ready":
            if (
                operation.selected_branch != "adaptive"
                or operation.adaptation_run_id != run.id
                or operation.state
                not in {"adapting", "applying", "reconciling"}
            ):
                raise MaxFinalizationConflict(
                    "restoration adaptation activation ownership changed"
                )
            if durable_cancel:
                operation.activation_cancel_requested_at = (
                    operation.activation_cancel_requested_at or datetime.now(UTC)
                )
                operation.error = None
                if operation.activation_request is None or has_proof_intent:
                    operation.state = "cancelled"
                    operation.phase = "activation_cancelled"
                    operation.activation_request = None
                    operation.activation_request_digest = None
                    root["restoration_adaptation_owner_status"] = "terminal_pending"
                    run.agent_state = root
                    run.status = "cancelled"
                    run.error = None
                    run.finished_at = datetime.now(UTC)
                    operation.activation_notification_state = (
                        operation.activation_notification_state or "pending"
                    )
                else:
                    operation.state = "reconciling"
                    operation.phase = "activation_cancel"
                _touch(operation)
            else:
                request = _activation_offer_request(
                    operation=operation,
                    run=run,
                    proof=state["restoration_adaptation_proof"],
                )
                exact_files = dict(files)
                intent = {
                    "offer_request": request.model_dump(mode="json"),
                    "candidate_files": exact_files,
                    "candidate_artifact_digest": canonical_files_digest(exact_files),
                }
                if operation.activation_request is None or has_proof_intent:
                    operation.activation_request = intent
                    operation.activation_request_digest = hashlib.sha256(
                        json.dumps(
                            intent, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                    operation.state = "applying"
                    operation.phase = "activation_offer_intent"
                    operation.error = None
                    _touch(operation)
                elif operation.activation_request == intent:
                    if operation.state == "adapting":
                        operation.state = "applying"
                        operation.phase = "activation_offer_intent"
                        operation.error = None
                        _touch(operation)
                elif "offer" not in operation.activation_request:
                    raise MaxFinalizationConflict(
                        "restoration adaptation activation intent changed"
                    )
        elif has_proof_intent:
            operation.activation_request = None
            operation.activation_request_digest = None
            if issued_failed_recovery:
                operation.state = "failed"
                operation.phase = "generation"
                reason = receipt.reason_code or receipt.state
                operation.error = (
                    f"restoration adaptation proof {receipt.state}: {reason}"
                )[:2000]
                operation.activation_notification_state = "pending"
                root["restoration_adaptation_owner_status"] = "terminal_pending"
                run.agent_state = root
            else:
                operation.state = "adapting"
                operation.phase = "generation"
                operation.error = None
            _touch(operation)
        if receipt.state != "proof_ready":
            # Back to repairs: the sealed wait is not charged to their window.
            note_proof_settled(run)
        await session.commit()


    async def _finalize_adaptation(
        self,
        *,
        files: Mapping[str, str],
        prompt: str,
    ) -> MaxFinalizationOutcome:
        self._last_files = dict(files)
        self._last_prompt = prompt
        identity = await self._identity()
        if workspace_revision_digest(files) != identity.workspace_revision:
            raise MaxFinalizationConflict("source files do not match the candidate revision")
        proof = self._transient_proof(identity)
        source_gap = max_source_completion_gap(prompt, files, portable=True)
        if source_gap is None:
            source_gap = restoration_probe_source_gap(dict(files))
        if source_gap is None:
            try:
                source_gap = await self._adaptation_capability_gap(files)
            except AdaptationBaselineUnavailable as exc:
                outcome = await self._outcome(
                    MaxFinalizationStatus.FAILED,
                    self._checkpoint(identity, GenerationPhase.EDIT),
                    ProofBundle(identity=proof),
                    str(exc),
                )
                await self._log_terminal(outcome)
                return outcome
        if source_gap is not None:
            return await self._outcome(
                MaxFinalizationStatus.NEEDS_EDIT,
                self._checkpoint(identity, GenerationPhase.EDIT),
                ProofBundle(identity=proof),
                source_gap,
            )

        # Phases are recorded here as on the ordinary path: a deadline or a crash then
        # names the check that was running instead of the last edit checkpoint.
        await self._phase_started(
            GenerationPhase.PREPARE, self._checkpoint(identity, GenerationPhase.PREPARE)
        )
        bootstrap, identity, proof = await self._adaptation_role_result(
            identity=identity,
            proof=proof,
            dimension=ProofDimension.BOOTSTRAP,
            role=ProjectCellCommandRole.BOOTSTRAP,
        )
        if bootstrap.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.PREPARE,
                bootstrap,
                bootstrap.redacted_detail,
            )
        await self._phase_finished(GenerationPhase.PREPARE)
        await self._phase_started(
            GenerationPhase.FINAL_BUILD, self._checkpoint(identity, GenerationPhase.FINAL_BUILD)
        )
        build, _after, _proof = await self._adaptation_role_result(
            identity=identity,
            proof=proof,
            dimension=ProofDimension.FULL_BUILD,
            role=ProjectCellCommandRole.FULL_BUILD,
        )
        bundle = ProofBundle(identity=proof, bootstrap=bootstrap, full_build=build)
        if build.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.FINAL_BUILD,
                build,
                build.redacted_detail,
                bundle=bundle,
            )
        await self._phase_finished(GenerationPhase.FINAL_BUILD)
        build_digest = _artifact_digest(build)
        await self._phase_started(
            GenerationPhase.RUNTIME_PROBE,
            self._checkpoint(identity, GenerationPhase.RUNTIME_PROBE),
        )
        runtime = await self._adaptation_runtime_result(
            identity=identity,
            proof=proof,
            build_digest=build_digest,
        )
        bundle = ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
        )
        if runtime.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.RUNTIME_PROBE,
                runtime,
                runtime.redacted_detail,
                bundle=bundle,
            )
        release = await self._adaptation_release_result(
            identity=identity,
            bundle=bundle,
            build_digest=build_digest,
        )
        bundle = ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
            release=release,
        )
        if release.outcome != ProofOutcome.GREEN.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.RUNTIME_PROBE,
                release,
                release.redacted_detail,
                bundle=bundle,
            )
        await self._phase_finished(GenerationPhase.RUNTIME_PROBE)
        final_files = await self._verified_workspace_files(identity)
        if canonical_files_digest(final_files) != build_digest:
            raise MaxFinalizationConflict("candidate artifact changed after release proof")
        self._last_files = final_files
        await self._phase_started(
            GenerationPhase.PROMOTE, self._checkpoint(identity, GenerationPhase.PROMOTE)
        )
        prove = self.executor.prove_restoration_adaptation
        assert prove is not None
        proof_attempt = await self._issue_adaptation_proof_attempt(
            identity=identity,
            artifact_digest=build_digest,
            files=final_files,
        )
        try:
            preservation = await prove(identity, build_digest, proof_attempt)
        except Exception as exc:
            raise AdaptationActivationRecoveryRequired(
                "restoration adaptation proof request requires recovery"
            ) from exc
        try:
            await self._persist_adaptation_proof(preservation, final_files)
        except Exception as exc:
            if preservation.state == "proof_ready":
                raise AdaptationActivationRecoveryRequired(
                    "sealed restoration adaptation proof requires recovery"
                ) from exc
            raise
        await self._phase_finished(GenerationPhase.PROMOTE)
        if preservation.state == "migration_required":
            return await self._outcome(
                MaxFinalizationStatus.NEEDS_EDIT,
                self._checkpoint(identity, GenerationPhase.EDIT),
                bundle,
                _migration_required_reason(preservation),
            )
        if preservation.state == "source_changed":
            outcome = await self._outcome(
                MaxFinalizationStatus.FAILED,
                self._checkpoint(identity, GenerationPhase.PROMOTE),
                bundle,
                f"source_changed:{preservation.reason_code or 'source_changed'}",
            )
            await self._log_terminal(outcome)
            return outcome
        proof_gap = _adaptation_proof_capability_gap(
            (await self._adaptation_bundle()),
            preservation.capabilities,
        )
        if proof_gap is not None:
            outcome = await self._outcome(
                MaxFinalizationStatus.FAILED,
                self._checkpoint(identity, GenerationPhase.PROMOTE),
                bundle,
                proof_gap,
            )
            await self._log_terminal(outcome)
            return outcome
        from yleum_api.services.restorations import activate_restoration_adaptation

        try:
            activated = await activate_restoration_adaptation(
                self.session_factory,
                generation_run_id=self.generation_run_id,
                files=final_files,
            )
        except Exception as exc:
            raise AdaptationActivationRecoveryRequired(
                "sealed restoration adaptation activation requires recovery"
            ) from exc
        cancelled = False
        if not activated:
            async with self.session_factory() as session:
                terminal_run = await session.get(GenerationRun, self.generation_run_id)
                terminal_state = (
                    terminal_run.agent_state
                    if terminal_run is not None and isinstance(terminal_run.agent_state, dict)
                    else {}
                )
                terminal_activation = terminal_state.get(
                    "restoration_adaptation_activation"
                )
                cancelled = bool(
                    terminal_run is not None
                    and terminal_run.status == "cancelled"
                    and isinstance(terminal_activation, dict)
                    and terminal_activation.get("state") == "cancelled"
                )
        status = (
            MaxFinalizationStatus.COMPLETE
            if activated
            else MaxFinalizationStatus.CANCELLED
            if cancelled
            else MaxFinalizationStatus.ACTIVATING
        )
        checkpoint = self._checkpoint(
            identity,
            GenerationPhase.COMPLETE if activated else GenerationPhase.PROMOTE,
        )
        outcome = await self._outcome(
            status,
            checkpoint,
            bundle,
            (
                "adaptation activation completed"
                if activated
                else "adaptation activation cancelled"
                if cancelled
                else "adaptation activation is awaiting reconciliation"
            ),
        )
        if activated:
            await self._log_terminal(outcome)
        return outcome

    async def _adaptation_bundle(self) -> object:
        async with self.session_factory() as session:
            run = await session.get(GenerationRun, self.generation_run_id)
            state = run.agent_state if run is not None else None
            return state.get("restoration_adaptation") if isinstance(state, dict) else None

    async def resume(
        self,
        checkpoint: MaxFinalizationCheckpoint,
        *,
        files: Mapping[str, str] | None = None,
        prompt: str | None = None,
    ) -> MaxFinalizationOutcome:
        if (
            checkpoint.generation_run_id != self.generation_run_id
            or checkpoint.workspace_id != self.executor.workspace_id
        ):
            raise MaxFinalizationConflict("checkpoint belongs to another generation")
        identity = await self._identity()
        if identity.proof_key != checkpoint.proof_key:
            raise MaxFinalizationConflict("checkpoint proof identity or fence changed")
        proof = await self._proof(identity)
        build = await self._find(proof, ProofDimension.FULL_BUILD)
        current_files = await self._verified_workspace_files(identity)
        current_digest = canonical_files_digest(current_files)
        build_is_current = build is not None and _current_build_result(build, current_digest)
        if build_is_current and build is not None and build.outcome == ProofOutcome.RED.value:
            return await self._failed(
                identity,
                proof,
                GenerationPhase.FINAL_BUILD,
                build,
                build.redacted_detail,
                bundle=ProofBundle(identity=proof, full_build=build),
            )
        if checkpoint.phase is GenerationPhase.COMPLETE and checkpoint.candidate_id:
            async with self.session_factory() as session:
                candidate = await session.get(ProjectCellCandidate, checkpoint.candidate_id)
                if candidate is None or candidate.status != "accepted":
                    raise MaxFinalizationConflict("accepted candidate checkpoint is stale")
            bundle = (
                await self._load_bundle(proof) if build_is_current else ProofBundle(identity=proof)
            )
            if bundle.permit is not None:
                return await self._outcome(
                    MaxFinalizationStatus.COMPLETE,
                    checkpoint,
                    bundle,
                    "final proof already accepted",
                )
        effective_files = dict(files) if files is not None else self._last_files
        effective_prompt = prompt if prompt is not None else self._last_prompt
        if effective_files is None or effective_prompt is None:
            raise MaxFinalizationConflict("resume requires source inputs for unfinished work")
        return await self.finalize(files=effective_files, prompt=effective_prompt)

    async def _identity(self) -> ProofIdentity:
        assert self.executor.current_identity is not None
        identity = await self.executor.current_identity()
        if (
            identity.workspace_id != self.executor.workspace_id
            or identity.generation_run_id != self.generation_run_id
        ):
            raise MaxFinalizationConflict("executor returned a foreign proof identity")
        return identity

    async def _verified_workspace_files(
        self,
        identity: ProofIdentity,
    ) -> dict[str, str]:
        reader = self.executor.refresh_snapshot_files or self.executor.snapshot_files
        files = dict(await reader())
        current = await self._identity()
        if current != identity:
            raise MaxFinalizationConflict("workspace identity changed while reading artifact")
        if workspace_revision_digest(files) != identity.workspace_revision:
            raise MaxFinalizationConflict("workspace files do not match the active revision")
        return files

    async def _proof(self, identity: ProofIdentity) -> ProjectCellProof:
        async with self.session_factory() as session:
            proof = await create_proof_identity(session, identity=identity)
            await session.commit()
            await session.refresh(proof)
            session.expunge(proof)
            return proof

    async def _find(
        self,
        proof: ProjectCellProof,
        dimension: ProofDimension,
        *,
        artifact_digest: str | None = None,
    ) -> ProjectCellProofResult | None:
        async with self.session_factory() as session:
            result = await find_proof_result(
                session,
                proof=proof,
                dimension=dimension,
                artifact_digest=artifact_digest,
            )
            if result is not None:
                session.expunge(result)
            return result

    async def _run_bootstrap(
        self,
        proof: ProjectCellProof,
        identity: ProofIdentity,
    ) -> tuple[ProjectCellProofResult, ProofIdentity, ProjectCellProof]:
        observation = await self._execute_role(
            identity=identity,
            dimension=ProofDimension.BOOTSTRAP,
            role=ProjectCellCommandRole.BOOTSTRAP,
            phase=GenerationPhase.PREPARE,
        )
        final_identity = observation.after
        final_proof = (
            proof
            if final_identity.proof_key == identity.proof_key
            else await self._proof(final_identity)
        )
        result = await self._record(
            proof=final_proof,
            dimension=ProofDimension.BOOTSTRAP,
            outcome=ProofOutcome.GREEN if observation.ok else ProofOutcome.RED,
            operation_id=observation.operation_id,
            artifact_ref=None,
            detail=observation.redacted_detail,
        )
        return result, final_identity, final_proof

    async def _run_role_result(
        self,
        *,
        proof: ProjectCellProof,
        identity: ProofIdentity,
        dimension: ProofDimension,
        role: ProjectCellCommandRole,
        phase: GenerationPhase,
    ) -> ProjectCellProofResult:
        observation = await self._execute_role(
            identity=identity,
            dimension=dimension,
            role=role,
            phase=phase,
        )
        if observation.after.proof_key != identity.proof_key:
            detail = "command changed the frozen proof identity"
            if observation.redacted_detail:
                detail += "\n" + observation.redacted_detail
            outcome = ProofOutcome.RED
        else:
            detail = observation.redacted_detail
            outcome = ProofOutcome.GREEN if observation.ok else ProofOutcome.RED
        artifact_ref = None
        if dimension is ProofDimension.FULL_BUILD and outcome is ProofOutcome.GREEN:
            artifact_files = await self._verified_workspace_files(identity)
            digest = canonical_files_digest(artifact_files)
            self._last_files = artifact_files
            artifact_ref = f"build/sha256/{digest}"
        if dimension is ProofDimension.FULL_BUILD:
            detail = _versioned_build_detail(detail)
        return await self._record(
            proof=proof,
            dimension=dimension,
            outcome=outcome,
            operation_id=observation.operation_id,
            artifact_ref=artifact_ref,
            detail=detail,
            refresh_incompatible=dimension is ProofDimension.FULL_BUILD,
        )

    async def _execute_role(
        self,
        *,
        identity: ProofIdentity,
        dimension: ProofDimension,
        role: ProjectCellCommandRole,
        phase: GenerationPhase,
    ) -> ProjectCellCommandObservation:
        # Proof reuse is dimension-specific; commands carry the full fenced
        # envelope and must never alias after another identity field changes.
        contract_version = (
            MAX_FULL_BUILD_CONTRACT_VERSION if role is ProjectCellCommandRole.FULL_BUILD else "v1"
        )
        operation_id = uuid5(
            self.generation_run_id,
            "command:"
            f"{identity.workspace_id}:{identity.fencing_epoch}:{identity.proof_key}:"
            f"{role.value}:{contract_version}",
        )
        await self._phase_started(phase, self._checkpoint(identity, phase, operation_id))
        run_role = self.executor.run_role
        operation_status = self.executor.operation_status
        assert run_role is not None
        assert operation_status is not None

        async def run_command() -> ProjectCellCommandObservation:
            return await run_role(role, operation_id)

        async def replay_command(status: object) -> ProjectCellCommandObservation:
            from yleum_api.services.orchestrator_client import ProjectCellAgentOperationStatus

            replay = self.executor.replay_role_response
            if not isinstance(status, ProjectCellAgentOperationStatus) or replay is None:
                raise ProjectCellInfrastructureError("activity_replay_unavailable", operation_id)
            response = status.terminal_response
            if response is None or response.operation_id != operation_id:
                raise ProjectCellInfrastructureError("activity_replay_unavailable", operation_id)
            observation = await replay(role, response, identity)
            if observation.before != identity:
                raise MaxFinalizationConflict("command replay proof identity mismatch")
            return observation

        observation = await run_with_activity_lease(
            session_factory=self.session_factory,
            lease=ActivityStart(
                operation_id=operation_id,
                workspace_id=identity.workspace_id,
                generation_run_id=identity.generation_run_id,
                kind=ActivityKind.COMMAND,
                fencing_epoch=identity.fencing_epoch,
                proof_key=identity.proof_key,
                phase=phase.value,
                deadline_at=datetime.now(UTC)
                + timedelta(seconds=get_settings().max_generation_deadline_seconds),
            ),
            work=run_command,
            replay_terminal=replay_command,
            poll_status=operation_status,
            emit=self.emit,
            heartbeat_seconds=get_settings().project_cell_heartbeat_seconds,
            terminal_state=lambda result: (
                ActivityState.TIMED_OUT
                if result.timed_out
                else ActivityState.COMPLETED
                if result.ok
                else ActivityState.FAILED
            ),
        )
        await self._counter(
            "bootstrap"
            if dimension is ProofDimension.BOOTSTRAP
            else "full_build"
            if dimension is ProofDimension.FULL_BUILD
            else "fast_check"
        )
        await self._phase_finished(phase)
        return observation

    async def _run_runtime(
        self,
        proof: ProjectCellProof,
        identity: ProofIdentity,
        build_digest: str,
        *,
        refresh_incompatible: bool = False,
    ) -> ProjectCellProofResult:
        dimension_key = identity.dimension_key(
            ProofDimension.RUNTIME,
            artifact_digest=build_digest,
        )
        operation_id = uuid5(
            self.generation_run_id,
            f"runtime:{MAX_FULL_BUILD_CONTRACT_VERSION}:{dimension_key}",
        )
        phase = GenerationPhase.RUNTIME_PROBE
        await self._phase_started(phase, self._checkpoint(identity, phase, operation_id))
        runtime_probe = self.executor.runtime_probe
        assert runtime_probe is not None

        async def run_probe() -> MaxRuntimeProbe:
            return cast(MaxRuntimeProbe, await runtime_probe(identity.proof_key))

        async def local_status(_operation_id: UUID) -> _LocalActivityStatus:
            return _LocalActivityStatus(
                state="running",
                phase=phase.value,
                heartbeat_at=datetime.now(UTC),
            )

        probe = await run_with_activity_lease(
            session_factory=self.session_factory,
            lease=ActivityStart(
                operation_id=operation_id,
                workspace_id=identity.workspace_id,
                generation_run_id=identity.generation_run_id,
                kind=ActivityKind.TOOL,
                fencing_epoch=identity.fencing_epoch,
                proof_key=identity.proof_key,
                phase=phase.value,
                deadline_at=datetime.now(UTC)
                + timedelta(seconds=get_settings().max_generation_deadline_seconds),
            ),
            work=run_probe,
            poll_status=local_status,
            emit=self.emit,
            heartbeat_seconds=get_settings().project_cell_heartbeat_seconds,
            terminal_state=lambda result: (
                ActivityState.COMPLETED if result.ok else ActivityState.FAILED
            ),
        )
        ok = bool(probe.ok)
        detail = str(probe.detail)
        await self._counter("runtime_probe")
        await self._phase_finished(phase)
        verification_digest = _content_digest(
            "runtime",
            identity.proof_key,
            build_digest,
            str(getattr(probe, "artifact_digest", "")),
        )
        return await self._record(
            proof=proof,
            dimension=ProofDimension.RUNTIME,
            outcome=ProofOutcome.GREEN if ok else ProofOutcome.RED,
            operation_id=operation_id,
            artifact_ref=f"verification/sha256/{verification_digest}" if ok else None,
            detail=detail,
            artifact_digest=build_digest,
            refresh_incompatible=refresh_incompatible,
        )

    async def _record_release(
        self,
        bundle: ProofBundle,
        build_digest: str,
    ) -> ProjectCellProofResult:
        from yleum_api.services.release_proof import run_release_proof

        verdict = await run_release_proof(
            self.project_id,
            self.project_slug,
            proof=bundle,
            require_max_data=True,
            project_cell_handle=self.executor,
        )
        dimension_key = bundle.identity.proof_key + build_digest
        operation_id = uuid5(self.generation_run_id, f"release:{dimension_key}")
        detail = bounded_redacted_text(verdict.summary, max_bytes=_MAX_DETAIL_BYTES)
        digest = release_receipt_digest(
            proof_key=bundle.identity.proof_key,
            artifact_digest=build_digest,
            detail=detail,
        )
        return await self._record(
            proof=bundle.identity,
            dimension=ProofDimension.RELEASE,
            outcome=ProofOutcome.GREEN if verdict.passed else ProofOutcome.RED,
            operation_id=operation_id,
            artifact_ref=release_receipt_ref(
                artifact_digest=build_digest,
                receipt_digest=digest,
            ),
            detail=detail,
            artifact_digest=build_digest,
            refresh_incompatible=True,
        )

    async def _record(
        self,
        *,
        proof: ProjectCellProof,
        dimension: ProofDimension,
        outcome: ProofOutcome,
        operation_id: UUID,
        artifact_ref: str | None,
        detail: str,
        artifact_digest: str | None = None,
        refresh_incompatible: bool = False,
    ) -> ProjectCellProofResult:
        detail_text = bounded_redacted_text(detail, max_bytes=_MAX_DETAIL_BYTES)

        def needs_refresh(result: ProjectCellProofResult) -> bool:
            return (
                result.outcome != outcome.value
                or result.operation_id != operation_id
                or result.artifact_ref != artifact_ref
                or result.redacted_detail != detail_text
            )

        async with self.session_factory() as session:
            stored_result: ProjectCellProofResult | None
            try:
                stored_result = await record_proof_result(
                    session,
                    proof=proof,
                    dimension=dimension,
                    outcome=outcome,
                    operation_id=operation_id,
                    artifact_ref=artifact_ref,
                    detail=detail,
                    artifact_digest=artifact_digest,
                )
                await session.commit()
                await session.refresh(stored_result)
            except ProjectCellProofConflict:
                await session.rollback()
                stored_result = await find_proof_result(
                    session,
                    proof=proof,
                    dimension=dimension,
                    artifact_digest=artifact_digest,
                )
                if stored_result is None:
                    raise
                if refresh_incompatible and needs_refresh(stored_result):
                    locked = await session.scalar(
                        select(ProjectCellProofResult)
                        .where(ProjectCellProofResult.id == stored_result.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if locked is None:
                        raise MaxFinalizationConflict(
                            "proof result disappeared during refresh"
                        ) from None
                    if needs_refresh(locked):
                        locked.outcome = outcome.value
                        locked.operation_id = operation_id
                        locked.artifact_ref = artifact_ref
                        locked.redacted_detail = detail_text
                        locked.detail_digest = hashlib.sha256(
                            detail_text.encode("utf-8")
                        ).hexdigest()
                        await session.commit()
                        await session.refresh(locked)
                    stored_result = locked
            session.expunge(stored_result)
            return stored_result

    async def _prepare_and_promote(
        self,
        identity: ProofIdentity,
        build: ProjectCellProofResult,
        release: ProjectCellProofResult,
        permit: PromotionPermit | None,
    ) -> ProjectCellCandidate:
        require_promotion_permit(permit, current_identity=await self._identity())
        build_ref = build.artifact_ref
        verification_ref = release.artifact_ref
        if build_ref is None or verification_ref is None:
            raise MaxFinalizationConflict("green release evidence lacks artifact refs")
        assert permit is not None
        if permit.build_ref != build_ref or permit.verification_ref != verification_ref:
            raise PromotionPermitError(
                "PROMOTION_PERMIT_STALE",
                "promotion permit does not match candidate artifact refs",
            )
        snapshot_operation = uuid5(
            self.generation_run_id,
            f"snapshot:{identity.proof_key}",
        )
        await self._phase_started(
            GenerationPhase.SNAPSHOT,
            self._checkpoint(identity, GenerationPhase.SNAPSHOT, snapshot_operation),
        )
        await self._ensure_activity(
            operation_id=snapshot_operation,
            identity=identity,
            kind=ActivityKind.SNAPSHOT,
            phase=GenerationPhase.SNAPSHOT,
            allow_completed_replay=True,
        )
        require_promotion_permit(permit, current_identity=await self._identity())
        async with self.session_factory() as session:
            candidate = await prepare_candidate(
                session,
                workspace_id=identity.workspace_id,
                generation_run_id=identity.generation_run_id,
                fencing_epoch=identity.fencing_epoch,
                source_revision=identity.workspace_revision,
                migration_digest=identity.schema_data_digest,
                database_backup_ref=(
                    "database-backup/sha256/"
                    + _content_digest("database", identity.schema_data_digest)
                ),
                build_ref=build_ref,
                verification_ref=verification_ref,
            )
            await session.commit()
            candidate_id = candidate.id
        await self._finish_activity(snapshot_operation, ActivityState.COMPLETED, "prepared")
        await self._phase_finished(GenerationPhase.SNAPSHOT)

        promotion_operation = uuid5(
            self.generation_run_id,
            f"promotion:{identity.proof_key}",
        )
        await self._phase_started(
            GenerationPhase.PROMOTE,
            self._checkpoint(
                identity,
                GenerationPhase.PROMOTE,
                promotion_operation,
                candidate_id,
            ),
        )
        await self._ensure_activity(
            operation_id=promotion_operation,
            identity=identity,
            kind=ActivityKind.PROMOTION,
            phase=GenerationPhase.PROMOTE,
            allow_completed_replay=True,
        )
        require_promotion_permit(permit, current_identity=await self._identity())
        async with self.session_factory() as session:
            candidate = await promote_candidate(
                session,
                candidate_id=candidate_id,
                generation_run_id=identity.generation_run_id,
                fencing_epoch=identity.fencing_epoch,
            )
            await session.commit()
            await session.refresh(candidate)
            session.expunge(candidate)
        await self._finish_activity(promotion_operation, ActivityState.COMPLETED, "accepted")
        await self._phase_finished(GenerationPhase.PROMOTE)
        return candidate

    async def _ensure_activity(
        self,
        *,
        operation_id: UUID,
        identity: ProofIdentity,
        kind: ActivityKind,
        phase: GenerationPhase,
        allow_completed_replay: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            existing = await session.get(ProjectCellActivityLease, operation_id)
            if existing is not None:
                if (
                    existing.workspace_id != identity.workspace_id
                    or existing.fencing_epoch != identity.fencing_epoch
                    or existing.proof_key != identity.proof_key
                ):
                    raise MaxFinalizationConflict("activity replay identity mismatch")
                if existing.state == ActivityState.ACTIVE.value:
                    await heartbeat_activity(
                        session,
                        operation_id=operation_id,
                        workspace_id=identity.workspace_id,
                        fencing_epoch=identity.fencing_epoch,
                        heartbeat_at=now,
                        phase=phase.value,
                    )
                    await session.commit()
                    return
                if allow_completed_replay and existing.state == ActivityState.COMPLETED.value:
                    return
                raise MaxFinalizationConflict("terminal activity has no proof result")
            try:
                await start_activity(
                    session,
                    operation_id=operation_id,
                    workspace_id=identity.workspace_id,
                    generation_run_id=identity.generation_run_id,
                    kind=kind,
                    fencing_epoch=identity.fencing_epoch,
                    proof_key=identity.proof_key,
                    phase=phase.value,
                    now=now,
                    deadline_at=now
                    + timedelta(seconds=get_settings().max_generation_deadline_seconds),
                )
                await session.commit()
            except ProjectCellActivityConflict:
                await session.rollback()
                raise

    async def _finish_activity(
        self,
        operation_id: UUID,
        state: ActivityState,
        detail: str,
    ) -> None:
        async with self.session_factory() as session:
            await finish_activity(
                session,
                operation_id=operation_id,
                state=state,
                finished_at=datetime.now(UTC),
                diagnostic=detail,
            )
            await session.commit()

    def _checkpoint(
        self,
        identity: ProofIdentity,
        phase: GenerationPhase,
        operation_id: UUID | None = None,
        candidate_id: UUID | None = None,
    ) -> MaxFinalizationCheckpoint:
        return MaxFinalizationCheckpoint(
            generation_run_id=self.generation_run_id,
            workspace_id=identity.workspace_id,
            proof_key=identity.proof_key,
            phase=phase,
            operation_id=operation_id,
            candidate_id=candidate_id,
            acceptance_id=_content_digest(
                "acceptance",
                str(self.generation_run_id),
                identity.proof_key,
            ),
        )

    async def _phase_started(
        self,
        phase: GenerationPhase,
        checkpoint: MaxFinalizationCheckpoint,
    ) -> None:
        async with self.session_factory() as session:
            run = await self._locked_run(session)
            state = run.agent_state.get("max_finalization", {})
            if not isinstance(state, dict) or state.get("current_phase") != phase.value:
                record_phase_started(run, phase)
            root = dict(run.agent_state)
            raw_finalization = root.get("max_finalization")
            finalization = dict(raw_finalization) if isinstance(raw_finalization, dict) else {}
            finalization["checkpoint"] = checkpoint.to_json()
            root["max_finalization"] = finalization
            run.agent_state = root
            await session.commit()
        await self.emit(
            "generation.phase",
            {
                "phase": phase.value,
                "proof_key": checkpoint.proof_key,
                "operation_id": (str(checkpoint.operation_id) if checkpoint.operation_id else None),
            },
        )

    async def _phase_finished(self, phase: GenerationPhase) -> None:
        async with self.session_factory() as session:
            run = await self._locked_run(session)
            state = run.agent_state.get("max_finalization", {})
            if isinstance(state, dict) and state.get("current_phase") == phase.value:
                if isinstance(state.get("current_phase_started_at_ms"), int):
                    record_phase_finished(run, phase)
            await session.commit()

    async def _counter(self, name: str) -> None:
        async with self.session_factory() as session:
            run = await self._locked_run(session)
            increment_generation_counter(run, name)
            await session.commit()

    async def _outcome(
        self,
        status: MaxFinalizationStatus,
        checkpoint: MaxFinalizationCheckpoint,
        proof: ProofBundle,
        detail: str,
    ) -> MaxFinalizationOutcome:
        safe_detail = bounded_redacted_text(detail, max_bytes=_MAX_DETAIL_BYTES)
        async with self.session_factory() as session:
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == self.generation_run_id)
                .with_for_update()
            )
            if run is None or run.project_id != self.project_id:
                raise MaxFinalizationConflict("generation run not found for project")
            raw_activation = (
                run.agent_state.get("restoration_adaptation_activation")
                if isinstance(run.agent_state, dict)
                else None
            )
            already_completed_activation = bool(
                status is MaxFinalizationStatus.COMPLETE
                and run.status == "completed"
                and isinstance(raw_activation, dict)
                and raw_activation.get("state") == "completed"
                and raw_activation.get("publication_consumed") is True
            )
            already_cancelled_activation = bool(
                status is MaxFinalizationStatus.CANCELLED
                and run.status == "cancelled"
                and isinstance(raw_activation, dict)
                and raw_activation.get("state") == "cancelled"
                and raw_activation.get("publication_consumed") is False
            )
            if run.status not in {"running", "cancel_requested"} and not (
                already_completed_activation or already_cancelled_activation
            ):
                raise MaxFinalizationConflict("generation run is not active")
            root = dict(run.agent_state)
            raw_state = root.get("max_finalization")
            state = dict(raw_state) if isinstance(raw_state, dict) else {}
            state["checkpoint"] = checkpoint.to_json()
            state["outcome"] = status.value
            root["max_finalization"] = state
            run.agent_state = root
            record_terminal_reason(
                run,
                (
                    None
                    if status
                    in {MaxFinalizationStatus.COMPLETE, MaxFinalizationStatus.ACTIVATING}
                    else safe_detail
                ),
            )
            await session.commit()
        return MaxFinalizationOutcome(status, checkpoint, proof, safe_detail)

    async def _failed(
        self,
        identity: ProofIdentity,
        proof: ProjectCellProof,
        phase: GenerationPhase,
        result: ProjectCellProofResult,
        detail: str,
        *,
        bundle: ProofBundle | None = None,
    ) -> MaxFinalizationOutcome:
        missing_build = (
            phase is GenerationPhase.FINAL_BUILD
            and "readiness failed:" in detail
            and "Could not find a production build" in detail
        )
        if missing_build:
            detail = (
                "Repair the test/manifest that removed the production build. "
                "Do not run next dev against the production .next directory after building. "
                "Use an isolated test distDir or test next start on a separate port, "
                "and terminate the test server. Then the coordinator will rebuild.\n" + detail
            )
        outcome = await self._outcome(
            MaxFinalizationStatus.NEEDS_EDIT if missing_build else MaxFinalizationStatus.FAILED,
            self._checkpoint(identity, phase, result.operation_id),
            bundle or ProofBundle(identity=proof),
            detail,
        )
        await self._log_terminal(outcome)
        return outcome

    async def _load_bundle(self, proof: ProjectCellProof) -> ProofBundle:
        bootstrap = await self._find(proof, ProofDimension.BOOTSTRAP)
        build = await self._find(proof, ProofDimension.FULL_BUILD)
        runtime = release = None
        if build is not None and build.artifact_ref:
            digest = _artifact_digest(build)
            runtime = await self._find(proof, ProofDimension.RUNTIME, artifact_digest=digest)
            release = await self._find(proof, ProofDimension.RELEASE, artifact_digest=digest)
            if release is not None and not release_receipt_matches(
                release,
                digest,
                proof.proof_key,
            ):
                release = None
        bundle = ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
            release=release,
        )
        try:
            permit = issue_promotion_permit(bundle)
        except PromotionPermitError:
            return bundle
        return ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
            release=release,
            permit=permit,
        )

    async def _locked_run(self, session: AsyncSession) -> GenerationRun:
        run = await session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == self.generation_run_id)
            .with_for_update()
        )
        if run is None or run.project_id != self.project_id:
            raise MaxFinalizationConflict("generation run not found for project")
        if run.status not in {"running", "cancel_requested"}:
            raise MaxFinalizationConflict("generation run is not active")
        return run

    async def _log_terminal(self, outcome: MaxFinalizationOutcome) -> None:
        async with self.session_factory() as session:
            run = await session.get(GenerationRun, self.generation_run_id)
            if run is not None:
                log_finalization_outcome(
                    run,
                    outcome=outcome.status.value,
                    proof_key=outcome.checkpoint.proof_key,
                    operation_id=outcome.checkpoint.operation_id,
                )
                await session.commit()


def proof_bundle_verdict(
    proof: ProofBundle,
    *,
    require_max_data: bool,
) -> FunctionalVerdict:
    return summarize(proof.release_checks(require_max_data=require_max_data))


_ACTIVE_RUN_STATUSES = frozenset({"pending", "queued_for_capacity", "running", "cancel_requested"})


def _migration_required_reason(preservation: RestorationAdaptationProof) -> str:
    """Причина для владельца и задание агенту на починку — с нарушенным правилом.

    Код причины сам по себе бывает слишком общим: годность манифеста проверки
    решают четырнадцать правил, и без имени нарушенного агент правит вслепую.
    Правило приходит уже проверенным по форме — строчные латинские слова.
    """
    reason = preservation.reason_code or "candidate_database_changed"
    detail = getattr(preservation, "reason_detail", None)
    return f"migration_required:{reason}" + (f" ({detail})" if detail else "")


async def generation_deadline_wait(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    generation_run_id: UUID,
    now: datetime | None = None,
) -> float | None:
    """Seconds until the watchdog has to look again; None once the run is over."""
    async with session_factory() as session:
        run = await session.get(GenerationRun, generation_run_id)
        if run is None or run.status not in _ACTIVE_RUN_STATUSES:
            return None
        deadline = generation_deadline(run)
    assert deadline.at is not None
    wait = max(0.0, (deadline.at - (now or datetime.now(UTC))).total_seconds())
    # A rejected proof puts the run back under the much nearer editing limit, so do not
    # sleep through the whole hand-off ceiling waiting to notice.
    return min(wait, _SEALED_RECHECK_SECONDS) if deadline.stage == "proof" else wait


async def run_generation_deadline_watchdog(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    generation_run_id: UUID,
) -> None:
    """Follow the run until it ends or its deadline is written.

    The deadline moves: an adaptation opens a repair window after the agent's turn and
    is exempt while its proof or activation intent is sealed, so one sleep is not enough.
    """
    while True:
        wait = await generation_deadline_wait(
            session_factory=session_factory,
            generation_run_id=generation_run_id,
        )
        if wait is None:
            return
        if wait > 0:
            await asyncio.sleep(wait)
            continue
        if await watch_generation_deadline(
            session_factory=session_factory,
            generation_run_id=generation_run_id,
        ):
            return
        # The run was sealed or finished between the two reads; look again shortly.
        await asyncio.sleep(1.0)


def _restoration_operation_id(run: GenerationRun) -> str | None:
    binding = run.agent_state.get("restoration_adaptation")
    if not isinstance(binding, dict) or binding.get("adaptation_run_id") != str(run.id):
        return None
    try:
        return str(UUID(str(binding["operation_id"])))
    except (KeyError, ValueError):
        return None


async def watch_generation_deadline(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    generation_run_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Write one terminal state when the deadline of the run's current stage expires."""
    current = now or datetime.now(UTC)
    async with session_factory() as session:
        run = await session.scalar(
            select(GenerationRun).where(GenerationRun.id == generation_run_id).with_for_update()
        )
        if run is None or run.status not in _ACTIVE_RUN_STATUSES:
            return False
        deadline = generation_deadline(run)
        if deadline.at is None or current < deadline.at:
            return False
        raw_state = run.agent_state.get("max_finalization", {})
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        raw_checkpoint = state.get("checkpoint", {})
        checkpoint = raw_checkpoint if isinstance(raw_checkpoint, dict) else {}
        # No checkpoint yet means finalization never began: the agent is still editing.
        phase = str(checkpoint.get("phase") or state.get("current_phase") or "edit")
        proof_key = str(checkpoint.get("proof_key") or "unknown")
        cancelled = run.status == "cancel_requested"
        leases = list(
            await session.scalars(
                select(ProjectCellActivityLease)
                .where(
                    ProjectCellActivityLease.generation_run_id == generation_run_id,
                    ProjectCellActivityLease.state == ActivityState.ACTIVE.value,
                )
                .order_by(ProjectCellActivityLease.started_at)
                .with_for_update()
            )
        )
        # The command or tool that was running, when there was one; a checkpoint that
        # carries no operation must not read as a lost restoration id.
        agent_operation_id = (
            str(leases[-1].operation_id) if leases else checkpoint.get("operation_id")
        )
        terminal = {
            "reason": "cancelled" if cancelled else "deadline",
            "stage": deadline.stage,
            "phase": phase,
            "restoration_operation_id": _restoration_operation_id(run),
            "agent_operation_id": str(agent_operation_id) if agent_operation_id else None,
            "proof_key": proof_key,
        }
        diagnostic = bounded_redacted_text(
            f"generation {'cancelled' if cancelled else 'deadline exceeded'}; "
            f"stage={terminal['stage']}; phase={phase}; "
            f"restoration_operation_id={terminal['restoration_operation_id'] or 'none'}; "
            f"agent_operation_id={terminal['agent_operation_id'] or 'none'}; "
            f"proof_key={proof_key}",
            max_bytes=_MAX_DETAIL_BYTES,
        )
        terminal_activity = (
            ActivityState.CANCELLED.value if cancelled else ActivityState.TIMED_OUT.value
        )
        for lease in leases:
            lease.state = terminal_activity
            lease.finished_at = max(current, lease.heartbeat_at)
            lease.heartbeat_at = lease.finished_at
            lease.redacted_diagnostic = diagnostic
        await terminalize_generation_run_locked(
            session,
            run,
            status="cancelled" if cancelled else "failed",
            error=diagnostic,
            finished_at=current,
        )
        state["outcome"] = run.status
        state["terminal_reason"] = diagnostic
        state["terminal"] = terminal
        root = dict(run.agent_state)
        root["max_finalization"] = state
        run.agent_state = root
        await session.commit()
        return True


__all__ = [
    "MaxFinalizationCheckpoint",
    "MaxFinalizationConflict",
    "MaxFinalizationCoordinator",
    "MaxFinalizationOutcome",
    "MaxFinalizationStatus",
    "ProofBundle",
    "generation_deadline_wait",
    "proof_bundle_verdict",
    "run_generation_deadline_watchdog",
    "watch_generation_deadline",
]
