"""Serialise deploy drain with new generation/restoration admission, not execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal
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


async def require_generation_admission(session: AsyncSession) -> None:
    from yleum_api.models.deployment_drain import DeploymentDrain

    # Held until the run/operation reservation commits. The deploy's exclusive
    # lock waits for every pre-fence reservation, closing check-zero/admit races.
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext(:key))"), {"key": _LOCK}
    )
    if await session.get(DeploymentDrain, _SCOPE, populate_existing=True) is not None:
        raise ApiError("generation_draining", "Generation admission is temporarily paused", 503)


async def _locked_drain(session: AsyncSession, release_sha: str) -> DeploymentDrain | None:
    from yleum_api.models.deployment_drain import DeploymentDrain

    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise ValueError("a full release SHA is required")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _LOCK})
    row = await session.get(DeploymentDrain, _SCOPE, populate_existing=True)
    if row is not None and row.release_sha != release_sha:
        raise ValueError("deployment drain belongs to another release")
    return row


async def begin_drain(session: AsyncSession, release_sha: str) -> None:
    from yleum_api.models.deployment_drain import DeploymentDrain

    if await _locked_drain(session, release_sha) is None:
        session.add(DeploymentDrain(scope=_SCOPE, release_sha=release_sha))
        await session.flush()


async def end_drain(session: AsyncSession, release_sha: str) -> None:
    row = await _locked_drain(session, release_sha)
    if row is not None:
        from yleum_api.models.generation_run import GenerationRun
        from yleum_api.services.generation.recovery_contract import (
            RecoveryContractError,
            RecoveryStateV2,
        )
        from yleum_api.services.generation.recovery_execution import recovery_state
        from yleum_api.services.generation.recovery_quiescence import quiesced_recovery_run

        saved = (await session.scalars(select(GenerationRun).where(
            GenerationRun.status.in_(("running", "cancel_requested")),
        ))).all()
        durable = [run for run in saved if isinstance(recovery_state(run), RecoveryStateV2)]
        if durable:
            snapshot = await require_recovery_readers(release_sha=release_sha, own=False)
            for run in durable:
                if await quiesced_recovery_run(
                    session, run, release_sha=release_sha, snapshot_digest=snapshot,
                ) is None:
                    raise RecoveryContractError("durable execution is not physically quiescent")
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
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate release capture field")
                result[key] = value
            return result

        if len(raw.encode("utf-8")) > 4096:
            raise ValueError("release capture context is too large")
        value = json.loads(raw, object_pairs_hook=unique)
        keys = {"version", "release_sha", "owner_nonce", "core_capture_sha256",
                "commerce_capture_sha256"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("release capture context shape is invalid")
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("release capture version is unsupported")
        for key in keys - {"version"}:
            if not isinstance(value[key], str):
                raise ValueError("release capture field type is invalid")
        if (
            value["release_sha"] != release_sha
            or re.fullmatch(r"[0-9a-f]{40}", value["release_sha"]) is None
            or str(UUID(value["owner_nonce"])) != value["owner_nonce"]
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

    @property
    def drained(self) -> bool:
        return not any(self.active.values())

    def to_wire(self) -> dict[str, object]:
        return {**asdict(self.capture), "basis": self.basis,
                "active": self.active, "drained": self.drained}


async def _resume_classification(
    session: AsyncSession,
    capture: ReleaseCaptureContext,
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
    for run in runs:
        if await _quiesced_physical_run(session, run) is not None:
            counts["generations"] -= 1
    return OwnedReleaseClassification(capture, counts)


async def _command(action: str, release_sha: str) -> None:
    from yleum_api.core.db import dispose_engine, get_engine

    capture = (
        ReleaseCaptureContext.parse(sys.stdin.read(4097), release_sha=release_sha)
        if action == "resume-check" else None
    )
    try:
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            if capture is not None:
                classified = await _resume_classification(session, capture)
                print(json.dumps(classified.to_wire(), sort_keys=True))
                if not classified.drained:
                    raise SystemExit(75)
                return
            if action == "begin":
                await begin_drain(session, release_sha)
            elif action == "end":
                await end_drain(session, release_sha)
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
        "action", choices=("begin", "check", "status", "end", "bootstrap-check", "resume-check"),
    )
    parser.add_argument("release_sha")
    arguments = parser.parse_args()
    asyncio.run(_command(arguments.action, arguments.release_sha))
