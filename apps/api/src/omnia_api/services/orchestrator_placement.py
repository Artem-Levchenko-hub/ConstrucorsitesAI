"""Choosing a host for a new Project Cell.

The binding is decided once, when the workspace row is created, and stays for
the life of the cell. Placement is least-loaded by live cells per host, scaled
by the host's weight; the default host wins ties so a single-host deployment
behaves exactly as before.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.services.orchestrator_hosts import OrchestratorRegistry, registry


async def live_cell_counts(session: AsyncSession) -> dict[str, int]:
    """Cells that occupy a host: every workspace row not yet deleted."""
    from omnia_api.models.project_cell import ProjectCellWorkspace

    rows = await session.execute(
        select(ProjectCellWorkspace.orchestrator, func.count())
        .where(
            ProjectCellWorkspace.deleted_at.is_(None),
            ProjectCellWorkspace.state != "deleted",
        )
        .group_by(ProjectCellWorkspace.orchestrator)
    )
    return {str(name): int(count) for name, count in rows.all()}


def pick_host(counts: dict[str, int], reg: OrchestratorRegistry) -> str:
    hosts = reg.enabled()
    if len(hosts) == 1:
        return hosts[0].name

    def load(name: str, weight: float) -> tuple[float, int, str]:
        # the default host sorts first among equals; then by name for determinism
        return (counts.get(name, 0) / weight, 0 if name == reg.default else 1, name)

    return min(hosts, key=lambda host: load(host.name, host.weight)).name


async def choose_orchestrator(session: AsyncSession) -> str:
    reg = registry()
    if reg.single:
        return reg.default
    return pick_host(await live_cell_counts(session), reg)


__all__ = ["choose_orchestrator", "live_cell_counts", "pick_host"]
