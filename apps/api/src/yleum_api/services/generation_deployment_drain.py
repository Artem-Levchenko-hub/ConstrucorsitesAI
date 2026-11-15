"""Serialise deploy drain with new generation/restoration admission, not execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.services.generation.recovery_readers import require_recovery_readers

if TYPE_CHECKING:
    from yleum_api.models.deployment_drain import DeploymentDrain
    from yleum_api.models.project_cell import ProjectCellOperation

_SCOPE = "generation"
_LOCK = "yleum:generation:deployment-drain:v1"


async def lock_generation_admission_gate(session: AsyncSession) -> None:
    """Serialize with drain state changes until this transaction commits."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext(:key))"), {"key": _LOCK}
    )


async def _current_drain(session: AsyncSession) -> DeploymentDrain | None:
    from yleum_api.models.deployment_drain import DeploymentDrain

    return await session.get(DeploymentDrain, _SCOPE, populate_existing=True)


async def require_generation_admission(session: AsyncSession) -> None:
    # The deploy's exclusive lock waits for every pre-fence reservation.
    await lock_generation_admission_gate(session)
    if await _current_drain(session) is not None:
        raise ApiError("generation_draining", "Generation admission is temporarily paused", 503)


async def try_continuation_admission(session: AsyncSession) -> bool:
    """Keep a successful shared barrier until the claim transaction ends."""
    # A continuation already owns a per-run session lock that the exclusive
    # deploy checker treats as active work. Do not wait on that checker.
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock_shared(hashtext(:key))"), {"key": _LOCK}
    )
    return acquired is True and await _current_drain(session) is None


async def _locked_drain(session: AsyncSession, release_sha: str) -> DeploymentDrain | None:
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise ValueError("a full release SHA is required")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _LOCK})
    row = await _current_drain(session)
    if row is not None and row.release_sha != release_sha:
        raise ValueError("deployment drain belongs to another release")
    return row


async def begin_drain(session: AsyncSession, release_sha: str) -> None:
    from yleum_api.models.deployment_drain import DeploymentDrain

    if await _locked_drain(session, release_sha) is None:
        session.add(DeploymentDrain(scope=_SCOPE, release_sha=release_sha))
        await session.flush()


async def end_drain(
    session: AsyncSession, release_sha: str, *, parked_source_run: UUID | None = None,
) -> None:
    row = await _locked_drain(session, release_sha)
    if row is not None:
        from yleum_api.models.generation_run import GenerationRun
        from yleum_api.services.generation.recovery_contract import (
            RecoveryContractError,
            RecoveryStateV2,
        )
        from yleum_api.services.generation.recovery_execution import recovery_state
        from yleum_api.services.generation.recovery_quiescence import (
            _quiesced_physical_run,
            quiesced_recovery_run,
        )

        saved = (await session.scalars(select(GenerationRun).where(
            GenerationRun.status.in_(("running", "cancel_requested")),
        ))).all()
        durable = [run for run in saved if isinstance(recovery_state(run), RecoveryStateV2)]
        if durable or parked_source_run is not None:
            snapshot = await require_recovery_readers(release_sha=release_sha, own=False)
            selected_verified = parked_source_run is None
            for run in durable:
                if parked_source_run is None:
                    verified = await quiesced_recovery_run(
                        session, run, release_sha=release_sha, snapshot_digest=snapshot,
                    ) is not None
                else:
                    selected = run.id == parked_source_run
                    verified = await _quiesced_physical_run(
                        session, run, parked_source="require" if selected else "deny",
                    ) is not None
                    selected_verified = selected_verified or (selected and verified)
                if not verified:
                    raise RecoveryContractError("durable execution is not physically quiescent")
            if not selected_verified:
                raise RecoveryContractError("selected parked source is not physically quiescent")
            # SQL/physical classification can outlive the observed inventory.
            # Keep this check under the same exclusive admission transaction.
            await require_recovery_readers(release_sha=release_sha, own=False)
        await session.delete(row)
        await session.flush()


def reconciliation_settles(target: ProjectCellOperation, receipt: ProjectCellOperation) -> bool:
    """Keep unknown history immutable; only its exact observed successor settles it."""
    from yleum_api.services.orchestrator_client import (
        OrchestratorUnavailable,
        ProjectCellResourceResponse,
    )
    from yleum_api.services.project_cells import ProjectCellValidationError, _stored_request_payload

    if (
        target.status != "indeterminate"
        or receipt.status != "completed"
        or receipt.kind != "reconcile"
        or receipt.workspace_id != target.workspace_id
        or target.fencing_epoch is None
        or receipt.fencing_epoch is None
        or receipt.fencing_epoch <= target.fencing_epoch
    ):
        return False
    try:
        if _stored_request_payload(receipt) != {"indeterminate_operation_id": str(target.id)}:
            return False
        result = dict(receipt.result_payload or {})
        if result.pop("reconciles_operation_id", None) != str(target.id):
            return False
        response = ProjectCellResourceResponse.from_json(result)
        return (
            response.workspace_id == target.workspace_id
            and response.fencing_epoch == receipt.fencing_epoch
            and response.state
            in {
                "retained",
                "resources_ready",
                "resources_paused",
                "degraded",
                "partial",
                "destroyed",
            }
        )
    except (ValueError, TypeError, ProjectCellValidationError, OrchestratorUnavailable):
        return False


async def unresolved_operation_count(session: AsyncSession) -> int:
    from yleum_api.models.project_cell import ProjectCellOperation

    unknown = list(
        (
            await session.scalars(
                select(ProjectCellOperation).where(ProjectCellOperation.status == "indeterminate")
            )
        ).all()
    )
    if not unknown:
        return 0
    receipts = list(
        (
            await session.scalars(
                select(ProjectCellOperation).where(
                    ProjectCellOperation.workspace_id.in_(
                        {operation.workspace_id for operation in unknown}
                    ),
                    ProjectCellOperation.kind == "reconcile",
                    ProjectCellOperation.status == "completed",
                )
            )
        ).all()
    )
    return sum(
        not any(reconciliation_settles(target, receipt) for receipt in receipts)
        for target in unknown
    )


async def _active_blockers(session: AsyncSession) -> dict[str, int]:
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellOperation
    from yleum_api.models.restoration import ACTIVE_RESTORATION_STATES, Restoration
    from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES

    counts = {}
    predicates = {
        "generations": (GenerationRun, GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES)),
        "operations": (
            ProjectCellOperation,
            ProjectCellOperation.status.in_(("pending", "waiting_capacity", "running")),
        ),
        "leases": (ProjectCellActivityLease, ProjectCellActivityLease.finished_at.is_(None)),
        "restorations": (Restoration, Restoration.state.in_(ACTIVE_RESTORATION_STATES)),
    }
    for key, (model, predicate) in predicates.items():
        counts[key] = int(
            await session.scalar(select(func.count()).select_from(model).where(predicate)) or 0
        )
    counts["operations"] += await unresolved_operation_count(session)
    return counts


async def drain_status(session: AsyncSession, *, bootstrap: bool = False) -> dict[str, object]:
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES

    row = None
    if not bootstrap:
        from yleum_api.models.deployment_drain import DeploymentDrain

        row = await session.get(DeploymentDrain, _SCOPE, populate_existing=True)
    counts = await _active_blockers(session)
    if row is not None and counts["generations"]:
        from yleum_api.services.generation.recovery_contract import RecoveryContractError
        from yleum_api.services.generation.recovery_quiescence import quiesced_recovery_run

        try:
            snapshot = await require_recovery_readers(own=False)
        except RecoveryContractError:
            snapshot = None
        if snapshot is not None:
            runs = (await session.scalars(select(GenerationRun).where(
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            ))).all()
            for run in runs:
                if await quiesced_recovery_run(
                    session, run, release_sha=row.release_sha, snapshot_digest=snapshot,
                ) is not None:
                    counts["generations"] -= 1
    return {
        "release_sha": row.release_sha if row else None,
        "active": counts,
        "drained": (bootstrap or row is not None) and not any(counts.values()),
        "bootstrap_readonly": bootstrap,
    }


def _release_context(raw: str, *, release_sha: str, fields: set[str]) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate release context field")
            result[key] = value
        return result

    if len(raw.encode("utf-8")) > 4096:
        raise ValueError("release context is too large")
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != {"version", "release_sha", *fields}:
        raise ValueError("release context shape is invalid")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("release context version is unsupported")
    if any(type(value[key]) is not str for key in {"release_sha", *fields}):
        raise ValueError("release context field type is invalid")
    if value["release_sha"] != release_sha or re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ValueError("release context target differs")
    return value


@dataclass(frozen=True, slots=True)
class ReleaseCaptureContext:
    """Operator context, never proof that the caller owns root files or a host."""

    version: Literal[1]
    release_sha: str
    owner_nonce: str
    core_capture_sha256: str
    commerce_capture_sha256: str

    @classmethod
    def parse(cls, raw: str, *, release_sha: str) -> ReleaseCaptureContext:
        value = _release_context(
            raw, release_sha=release_sha,
            fields={"owner_nonce", "core_capture_sha256", "commerce_capture_sha256"},
        )
        if (
            str(UUID(value["owner_nonce"])) != value["owner_nonce"]
            or any(re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
                   for key in ("core_capture_sha256", "commerce_capture_sha256"))
        ):
            raise ValueError("release capture context binding is invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class OwnedReleaseClassification:
    capture: ReleaseCaptureContext
    active: dict[str, int]
    basis: Literal["owned_release_capture"] = "owned_release_capture"
    parked_source_verified: bool | None = None

    @property
    def drained(self) -> bool:
        return self.parked_source_verified is not False and not any(self.active.values())

    def to_wire(self) -> dict[str, object]:
        result: dict[str, object] = {
            **asdict(self.capture), "basis": self.basis, "active": self.active,
            "drained": self.drained,
        }
        if self.parked_source_verified is not None:
            result["parked_source_verified"] = self.parked_source_verified
        return result


async def _resume_classification(
    session: AsyncSession,
    capture: ReleaseCaptureContext,
    *,
    parked_source_run: UUID | None = None,
) -> OwnedReleaseClassification:
    """CLI-only: the privileged launcher verifies both host captures/inhibits.

    This cannot establish reader compatibility or open admission. Its existing
    exclusive drain lock lasts until this read-only transaction is closed.
    """
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_quiescence import _quiesced_physical_run
    from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES

    await session.execute(text("SET TRANSACTION READ ONLY"))
    if await _locked_drain(session, capture.release_sha) is None:
        raise ValueError("resume classification requires its existing deployment drain")
    counts = await _active_blockers(session)
    runs = (await session.scalars(select(GenerationRun).where(
        GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
    ))).all()
    selected_verified: bool | None = None if parked_source_run is None else False
    for run in runs:
        if parked_source_run is None:
            evidence = await _quiesced_physical_run(session, run)
        else:
            selected = run.id == parked_source_run
            evidence = await _quiesced_physical_run(
                session, run, parked_source="require" if selected else "deny",
            )
            selected_verified = bool(selected_verified) or (selected and evidence is not None)
        if evidence is not None:
            counts["generations"] -= 1
    return OwnedReleaseClassification(capture, counts, parked_source_verified=selected_verified)


@dataclass(frozen=True, slots=True)
class ParkedSourceCheck:
    version: Literal[1]
    release_sha: str
    run_id: UUID

    @classmethod
    def parse(cls, raw: str, *, release_sha: str) -> ParkedSourceCheck:
        value = _release_context(raw, release_sha=release_sha, fields={"run_id"})
        run_id = UUID(value["run_id"])
        if str(run_id) != value["run_id"]:
            raise ValueError("parked source run ID is not canonical")
        return cls(version=1, release_sha=release_sha, run_id=run_id)


@dataclass(frozen=True, slots=True)
class ParkedSourceClassification:
    release_sha: str
    active: dict[str, int]
    selected_verified: bool

    @property
    def drained(self) -> bool:
        return self.selected_verified and not any(self.active.values())

    def to_wire(self) -> dict[str, object]:
        return {**asdict(self), "version": 1, "basis": "parked_source_recovery",
                "drained": self.drained}


async def _parked_source_classification(
    session: AsyncSession, context: ParkedSourceCheck,
) -> ParkedSourceClassification:
    """Classify one approved parked run with the real current reader set."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.generation.recovery_quiescence import _quiesced_physical_run
    from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES

    await session.execute(text("SET TRANSACTION READ ONLY"))
    if await _locked_drain(session, context.release_sha) is None:
        raise ValueError("parked source check requires its existing deployment drain")
    counts = await _active_blockers(session)
    quiescent = 0
    selected_verified = False
    try:
        await require_recovery_readers(own=False)
        runs = (await session.scalars(select(GenerationRun).where(
            GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
        ))).all()
        for run in runs:
            selected = run.id == context.run_id
            evidence = await _quiesced_physical_run(
                session, run, parked_source="require" if selected else "deny",
            )
            if evidence is not None:
                quiescent += 1
                selected_verified = selected_verified or selected
        await require_recovery_readers(own=False)
    except RecoveryContractError:
        return ParkedSourceClassification(context.release_sha, counts, False)
    counts["generations"] -= quiescent
    return ParkedSourceClassification(context.release_sha, counts, selected_verified)


async def _command(action: str, release_sha: str, selected_run: str | None = None) -> None:
    from yleum_api.core.db import dispose_engine, get_engine

    selected = UUID(selected_run) if selected_run is not None else None
    if (selected is not None) != (action in {"resume-parked-source-check", "end-parked-source"}):
        raise ValueError("parked source scope is required only for scoped drain actions")
    if selected is not None and str(selected) != selected_run:
        raise ValueError("parked source run ID is not canonical")
    capture = (
        ReleaseCaptureContext.parse(sys.stdin.read(4097), release_sha=release_sha)
        if action in {"resume-check", "resume-parked-source-check"} else None
    )
    parked = (
        ParkedSourceCheck.parse(sys.stdin.read(4097), release_sha=release_sha)
        if action == "parked-source-check" else None
    )
    try:
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            if parked is not None:
                parked_result = await _parked_source_classification(session, parked)
                print(json.dumps(parked_result.to_wire(), sort_keys=True))
                if not parked_result.drained:
                    raise SystemExit(75)
                return
            if capture is not None:
                classified = await _resume_classification(
                    session, capture, parked_source_run=selected,
                )
                print(json.dumps(classified.to_wire(), sort_keys=True))
                if not classified.drained:
                    raise SystemExit(75)
                return
            if action == "begin":
                await begin_drain(session, release_sha)
            elif action in {"end", "end-parked-source"}:
                await end_drain(session, release_sha, parked_source_run=selected)
            await session.commit()
            result = await drain_status(session, bootstrap=action == "bootstrap-check")
            print(json.dumps(result, sort_keys=True))
            if action == "check" and (
                result["release_sha"] != release_sha or not result["drained"]
            ):
                raise SystemExit(75)
            if action == "bootstrap-check" and not result["drained"]:
                raise SystemExit(75)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=(
            "begin", "check", "status", "end", "bootstrap-check", "resume-check",
            "parked-source-check", "resume-parked-source-check", "end-parked-source",
        ),
    )
    parser.add_argument("release_sha")
    parser.add_argument("selected_run", nargs="?")
    arguments = parser.parse_args()
    asyncio.run(_command(arguments.action, arguments.release_sha, arguments.selected_run))
