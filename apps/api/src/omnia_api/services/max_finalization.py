"""Single proof-carrying finalization owner for portable MAX generations."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellProofResult,
)
from omnia_api.services.agent_progress import bounded_redacted_text
from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize
from omnia_api.services.generation_metrics import (
    GenerationPhase,
    increment_generation_counter,
    log_finalization_outcome,
    record_phase_finished,
    record_phase_started,
    record_terminal_reason,
)
from omnia_api.services.max_generation_contract import max_source_completion_gap
from omnia_api.services.max_runtime_probe import MaxRuntimeProbe
from omnia_api.services.project_cell_activity import (
    ActivityKind,
    ActivityStart,
    ActivityState,
    ProjectCellActivityConflict,
    finish_activity,
    heartbeat_activity,
    run_with_activity_lease,
    start_activity,
)
from omnia_api.services.project_cell_candidates import prepare_candidate, promote_candidate
from omnia_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
    ProjectCellExecutorHandle,
)
from omnia_api.services.project_cell_proofs import (
    ProjectCellProofConflict,
    ProofDimension,
    ProofIdentity,
    ProofOutcome,
    create_proof_identity,
    find_proof_result,
    record_proof_result,
)

_MAX_DETAIL_BYTES = 4096


async def _discard_event(
    _event_type: str,
    _payload: Mapping[str, object],
) -> None:
    return None


class MaxFinalizationConflict(RuntimeError):
    """The durable checkpoint no longer belongs to the active fenced identity."""


class MaxFinalizationStatus(StrEnum):
    NEEDS_EDIT = "needs_edit"
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

    def release_checks(self, *, require_max_data: bool) -> list[Check]:
        checks = [
            _result_check("typecheck", self.full_build),
            _result_check("runtime", self.runtime),
        ]
        if require_max_data:
            checks.append(_result_check("max_data_plane", self.runtime))
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

    async def fast_check(self) -> ProjectCellProofResult:
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
        self._last_files = dict(files)
        self._last_prompt = prompt
        identity = await self._identity()
        proof = await self._proof(identity)
        checkpoint = self._checkpoint(identity, GenerationPhase.PREPARE)
        source_gap = max_source_completion_gap(prompt, files, portable=True)
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
        runtime = await self._find(
            proof,
            ProofDimension.RUNTIME,
            artifact_digest=build_digest,
        )
        if runtime is None:
            runtime = await self._run_runtime(proof, identity, build_digest)
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

        release = await self._find(
            proof,
            ProofDimension.RELEASE,
            artifact_digest=build_digest,
        )
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

        candidate = await self._prepare_and_promote(identity, build, release)
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
        """Return source feedback to the same editor, at most twice.

        Only NEEDS_EDIT is repairable here. Infrastructure/proof failures and
        cancellation stay terminal. Every pass observes the actual workspace;
        unchanged source cannot earn another build or an infinite model loop.
        """
        files = await self.executor.snapshot_files()
        for attempt in range(3):
            outcome = await self.finalize(files=files, prompt=prompt)
            if outcome.status is not MaxFinalizationStatus.NEEDS_EDIT or attempt == 2:
                return outcome
            async with self.session_factory() as session:
                run = await self._locked_run(session)
                if run.status == "cancel_requested":
                    raise asyncio.CancelledError
                deadline = (run.started_at or run.created_at) + timedelta(
                    seconds=get_settings().max_generation_deadline_seconds
                )
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                raise TimeoutError("generation deadline exceeded before source repair")
            async with asyncio.timeout(remaining):
                await repair(outcome.redacted_detail)
            updated = await self.executor.snapshot_files()
            if updated == files:
                return outcome
            files = updated
        raise AssertionError("bounded finalization loop exhausted")

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
        if build is not None and build.outcome == ProofOutcome.RED.value:
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
            return await self._outcome(
                MaxFinalizationStatus.COMPLETE,
                checkpoint,
                await self._load_bundle(proof),
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
            outcome = ProofOutcome.RED
        else:
            detail = observation.redacted_detail
            outcome = ProofOutcome.GREEN if observation.ok else ProofOutcome.RED
        artifact_ref = None
        if dimension is ProofDimension.FULL_BUILD and outcome is ProofOutcome.GREEN:
            digest = identity.dimension_key(ProofDimension.FULL_BUILD)
            artifact_ref = f"build/sha256/{digest}"
        return await self._record(
            proof=proof,
            dimension=dimension,
            outcome=outcome,
            operation_id=observation.operation_id,
            artifact_ref=artifact_ref,
            detail=detail,
        )

    async def _execute_role(
        self,
        *,
        identity: ProofIdentity,
        dimension: ProofDimension,
        role: ProjectCellCommandRole,
        phase: GenerationPhase,
    ) -> ProjectCellCommandObservation:
        dimension_key = identity.dimension_key(dimension)
        operation_id = uuid5(self.generation_run_id, f"{dimension.value}:{dimension_key}")
        await self._phase_started(phase, self._checkpoint(identity, phase, operation_id))
        run_role = self.executor.run_role
        operation_status = self.executor.operation_status
        assert run_role is not None
        assert operation_status is not None

        async def run_command() -> ProjectCellCommandObservation:
            return await run_role(role, operation_id)

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
            "bootstrap" if dimension is ProofDimension.BOOTSTRAP else "full_build"
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
    ) -> ProjectCellProofResult:
        dimension_key = identity.dimension_key(
            ProofDimension.RUNTIME,
            artifact_digest=build_digest,
        )
        operation_id = uuid5(self.generation_run_id, f"runtime:{dimension_key}")
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
        )

    async def _record_release(
        self,
        bundle: ProofBundle,
        build_digest: str,
    ) -> ProjectCellProofResult:
        from omnia_api.services.release_proof import run_release_proof

        verdict = await run_release_proof(
            self.project_id,
            self.project_slug,
            proof=bundle,
            require_max_data=True,
            project_cell_handle=self.executor,
        )
        dimension_key = bundle.identity.proof_key + build_digest
        operation_id = uuid5(self.generation_run_id, f"release:{dimension_key}")
        detail = verdict.summary
        digest = _content_digest("release", bundle.identity.proof_key, build_digest, detail)
        return await self._record(
            proof=bundle.identity,
            dimension=ProofDimension.RELEASE,
            outcome=ProofOutcome.GREEN if verdict.passed else ProofOutcome.RED,
            operation_id=operation_id,
            artifact_ref=f"verification/sha256/{digest}" if verdict.passed else None,
            detail=detail,
            artifact_digest=build_digest,
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
    ) -> ProjectCellProofResult:
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
            session.expunge(stored_result)
            return stored_result

    async def _prepare_and_promote(
        self,
        identity: ProofIdentity,
        build: ProjectCellProofResult,
        release: ProjectCellProofResult,
    ) -> ProjectCellCandidate:
        build_ref = build.artifact_ref
        verification_ref = release.artifact_ref
        if build_ref is None or verification_ref is None:
            raise MaxFinalizationConflict("green release evidence lacks artifact refs")
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
                if (
                    allow_completed_replay
                    and existing.state == ActivityState.COMPLETED.value
                ):
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
            finalization = (
                dict(raw_finalization) if isinstance(raw_finalization, dict) else {}
            )
            finalization["checkpoint"] = checkpoint.to_json()
            root["max_finalization"] = finalization
            run.agent_state = root
            await session.commit()
        await self.emit(
            "generation.phase",
            {
                "phase": phase.value,
                "proof_key": checkpoint.proof_key,
                "operation_id": (
                    str(checkpoint.operation_id) if checkpoint.operation_id else None
                ),
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
            run = await self._locked_run(session)
            root = dict(run.agent_state)
            raw_state = root.get("max_finalization")
            state = dict(raw_state) if isinstance(raw_state, dict) else {}
            state["checkpoint"] = checkpoint.to_json()
            state["outcome"] = status.value
            root["max_finalization"] = state
            run.agent_state = root
            record_terminal_reason(
                run,
                None if status is MaxFinalizationStatus.COMPLETE else safe_detail,
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
        outcome = await self._outcome(
            MaxFinalizationStatus.FAILED,
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
        return ProofBundle(
            identity=proof,
            bootstrap=bootstrap,
            full_build=build,
            runtime=runtime,
            release=release,
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


async def watch_generation_deadline(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    generation_run_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Write one terminal state when the overall generation deadline expires."""
    current = now or datetime.now(UTC)
    async with session_factory() as session:
        run = await session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == generation_run_id)
            .with_for_update()
        )
        if run is None or run.status not in {
            "pending",
            "queued_for_capacity",
            "running",
            "cancel_requested",
        }:
            return False
        started = run.started_at or run.created_at
        deadline = started + timedelta(
            seconds=get_settings().max_generation_deadline_seconds
        )
        if current < deadline:
            return False
        raw_state = run.agent_state.get("max_finalization", {})
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        raw_checkpoint = state.get("checkpoint", {})
        checkpoint = raw_checkpoint if isinstance(raw_checkpoint, dict) else {}
        phase = str(checkpoint.get("phase") or state.get("current_phase") or "unknown")
        proof_key = str(checkpoint.get("proof_key") or "unknown")
        operation_id = str(checkpoint.get("operation_id") or "unknown")
        cancelled = run.status == "cancel_requested"
        diagnostic = bounded_redacted_text(
            f"generation {'cancelled' if cancelled else 'deadline exceeded'}; "
            f"phase={phase}; proof_key={proof_key}; operation_id={operation_id}",
            max_bytes=_MAX_DETAIL_BYTES,
        )
        leases = list(
            await session.scalars(
                select(ProjectCellActivityLease)
                .where(
                    ProjectCellActivityLease.generation_run_id == generation_run_id,
                    ProjectCellActivityLease.state == ActivityState.ACTIVE.value,
                )
                .with_for_update()
            )
        )
        terminal_activity = (
            ActivityState.CANCELLED.value if cancelled else ActivityState.TIMED_OUT.value
        )
        for lease in leases:
            lease.state = terminal_activity
            lease.finished_at = max(current, lease.heartbeat_at)
            lease.heartbeat_at = lease.finished_at
            lease.redacted_diagnostic = diagnostic
        run.status = "cancelled" if cancelled else "failed"
        run.error = diagnostic
        run.finished_at = current
        state["outcome"] = run.status
        state["terminal_reason"] = diagnostic
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
    "proof_bundle_verdict",
    "watch_generation_deadline",
]
