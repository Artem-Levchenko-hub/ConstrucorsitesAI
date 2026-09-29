"""Serialise deploy drain with new generation/restoration admission, not execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError

if TYPE_CHECKING:
    from yleum_api.models.deployment_drain import DeploymentDrain

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
        await session.delete(row)
        await session.flush()


async def drain_status(
    session: AsyncSession, *, bootstrap: bool = False
) -> dict[str, object]:
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellOperation
    from yleum_api.models.restoration import ACTIVE_RESTORATION_STATES, Restoration
    from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES

    row = None
    if not bootstrap:
        from yleum_api.models.deployment_drain import DeploymentDrain

        row = await session.get(DeploymentDrain, _SCOPE, populate_existing=True)
    counts = {}
    predicates = {
        "generations": (GenerationRun, GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES)),
        "operations": (
            ProjectCellOperation,
            ProjectCellOperation.status.in_(
                ("pending", "waiting_capacity", "running", "indeterminate")
            ),
        ),
        "leases": (ProjectCellActivityLease, ProjectCellActivityLease.finished_at.is_(None)),
        "restorations": (Restoration, Restoration.state.in_(ACTIVE_RESTORATION_STATES)),
    }
    for key, (model, predicate) in predicates.items():
        counts[key] = int(
            await session.scalar(select(func.count()).select_from(model).where(predicate)) or 0
        )
    return {
        "release_sha": row.release_sha if row else None,
        "active": counts,
        "drained": (bootstrap or row is not None) and not any(counts.values()),
        "bootstrap_readonly": bootstrap,
    }


async def _command(action: str, release_sha: str) -> None:
    from yleum_api.core.db import dispose_engine, get_engine

    try:
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
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
    parser.add_argument("action", choices=("begin", "check", "status", "end", "bootstrap-check"))
    parser.add_argument("release_sha")
    arguments = parser.parse_args()
    asyncio.run(_command(arguments.action, arguments.release_sha))
