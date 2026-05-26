"""Pydantic schemas for V2 runtime/deploy endpoints.

These mirror the public TypeScript types in
`apps/web/src/lib/api/types.ts` (RuntimeStatus / DeployStatus) so the
api/web contract stays in sync. Bigger orchestrator payloads (e.g. logs
URL, image digest) are intentionally NOT in the public response — they
live inside the internal contract between api and orchestrator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RuntimeState = Literal["provisioning", "running", "paused", "stopped", "failed"]
DeployPhase = Literal["queued", "building", "pushing", "swapping", "done", "failed"]


class RuntimeStatus(BaseModel):
    """Public response of GET / POST /api/projects/:id/runtime*."""

    state: RuntimeState
    container_name: str | None = None
    port: int | None = None
    # Browser-reachable URL for the dev preview iframe. Until DNS / SSL is
    # wired up on the VPS, orchestrator returns the loopback form
    # (http://127.0.0.1:<port>) which is only useful for diagnostics — the
    # frontend will display "preview pending domain setup" in that case.
    dev_url: str | None = None
    last_active_at: str | None = None
    # Idle time before automatic hibernation (seconds, tier-dependent).
    hibernate_after_seconds: int | None = None


class RuntimeStopRequest(BaseModel):
    pause: bool = Field(
        default=True,
        description="True = docker pause (faster wake, keeps RAM). False = docker stop.",
    )


class DeployRequest(BaseModel):
    commit_sha: str | None = Field(
        default=None,
        description=(
            "Specific commit to deploy. Defaults to project HEAD when omitted."
        ),
    )


class DeployStatus(BaseModel):
    phase: DeployPhase
    started_at: str | None = None
    finished_at: str | None = None
    # Public prod URL once swap is done (or null until then).
    prod_url: str | None = None
    image_tag: str | None = None
    error: str | None = None


class RuntimeLogs(BaseModel):
    """Recent container stdout+stderr, capped at `tail` lines.

    Returned by GET /api/projects/:id/runtime/logs. `logs` is a single
    newline-joined string — the frontend renders it in a scrollable mono
    panel and (optionally) polls every few seconds for live updates.
    """

    container_name: str | None = None
    tail: int
    logs: str
