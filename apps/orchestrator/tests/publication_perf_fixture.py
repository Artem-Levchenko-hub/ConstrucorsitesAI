"""P00 helpers for publication performance measurements.

A profile is only ever built for a QA project listed in the baseline document;
timings that were not recorded stay ``None`` (never zero); the publication mode
comes from the journal state (``data_seeded`` / active release / desired
snapshot), never from a project name or label.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

BASELINE = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "operations"
    / "publication-performance-baseline.json"
)
MODES = frozenset({"first", "warm", "repeat", "config_only", "migration"})
_TIMING = re.compile(r"^(prepare|activate|total)_ms=(\d+)$")


class ForeignProjectError(ValueError):
    """A measurement was requested for a project that is not QA-owned."""


@dataclass(frozen=True)
class Timings:
    prepare_ms: int | None
    activate_ms: int | None
    total_ms: int | None


@dataclass(frozen=True)
class PublicationProfile:
    project_id: UUID
    mode: str
    timings: Timings


def load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def qa_project_ids(baseline: dict[str, Any] | None = None) -> frozenset[UUID]:
    data = baseline or load_baseline()
    return frozenset(UUID(item["project_id"]) for item in data["qa_projects"])


def require_qa_project(project_id: UUID, baseline: dict[str, Any] | None = None) -> UUID:
    if project_id not in qa_project_ids(baseline):
        raise ForeignProjectError(f"project {project_id} is not a QA measurement project")
    return project_id


def parse_timings(logs: list[str] | None) -> Timings:
    """``logs`` as the orchestrator writes them (``prepare_ms=…``). Anything
    missing is unknown, not zero."""
    found: dict[str, int] = {}
    for line in logs or []:
        match = _TIMING.match(line.strip())
        if match:
            found[match.group(1)] = int(match.group(2))
    return Timings(found.get("prepare"), found.get("activate"), found.get("total"))


def publication_mode(
    *,
    data_seeded: bool,
    active_release: dict[str, Any] | None,
    desired_snapshot_id: str,
    schema_compatible: bool = True,
    config_only: bool = False,
) -> str:
    if not data_seeded or active_release is None:
        return "first"
    if not schema_compatible:
        return "migration"
    if active_release.get("snapshot_id") == desired_snapshot_id:
        return "config_only" if config_only else "repeat"
    return "warm"


def profile(
    project_id: UUID,
    journal: dict[str, Any],
    *,
    desired_snapshot_id: str,
    logs: list[str] | None,
    baseline: dict[str, Any] | None = None,
) -> PublicationProfile:
    require_qa_project(project_id, baseline)
    mode = publication_mode(
        data_seeded=bool(journal.get("data_seeded")),
        active_release=journal.get("active_release"),
        desired_snapshot_id=desired_snapshot_id,
    )
    return PublicationProfile(project_id=project_id, mode=mode, timings=parse_timings(logs))
