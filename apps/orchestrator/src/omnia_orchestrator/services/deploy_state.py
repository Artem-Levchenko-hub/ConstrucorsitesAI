"""In-memory deploy state per project.

R-05 (YAGNI): no DB persistence yet. The orchestrator is single-process (see
`port_allocator`), so a module-level dict is enough. State is lost on restart,
which is acceptable for beta: a GET then returns the `queued` default and a
re-deploy repopulates it. Promote to Postgres only when deploy history becomes
a real requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

_ACTIVE_PHASES = ("queued", "building", "swapping")


@dataclass
class DeployRecord:
    project_id: str
    phase: str = "queued"
    prod_url: str | None = None
    image_tag: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


_records: dict[str, DeployRecord] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get(project_id: str) -> DeployRecord | None:
    return _records.get(project_id)


def start(project_id: str) -> DeployRecord:
    rec = DeployRecord(project_id=project_id, phase="building", started_at=now_iso())
    _records[project_id] = rec
    return rec


def update(project_id: str, **fields: object) -> None:
    rec = _records.get(project_id)
    if rec is None:
        return
    for key, value in fields.items():
        setattr(rec, key, value)


def is_active(project_id: str) -> bool:
    rec = _records.get(project_id)
    return rec is not None and rec.phase in _ACTIVE_PHASES
