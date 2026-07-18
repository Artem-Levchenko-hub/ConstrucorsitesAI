"""Pydantic DTOs for the internal orchestrator API.

These shapes are consumed by apps/api (which forwards from web). Keep in
sync with docs/01-api-contract.md V2 section.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Tier = Literal["free", "pro", "business"]
RuntimeState = Literal["provisioning", "running", "paused", "stopped", "failed"]


_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$"


class ProvisionRequest(BaseModel):
    project_id: UUID
    slug: str = Field(min_length=3, max_length=63, pattern=_SLUG_PATTERN)
    template: str  # e.g. "nextjs-postgres-drizzle"
    tier: Tier = "free"
    initial_env: dict[str, str] = Field(default_factory=dict)


class ProvisionResponse(BaseModel):
    project_id: UUID
    container_name: str
    port: int
    dev_url: str  # https://<slug>.preview.<base_domain>
    state: RuntimeState


class WakeRequest(BaseModel):
    project_id: UUID


class WakeResponse(BaseModel):
    project_id: UUID
    state: RuntimeState
    ready_in_seconds: int  # estimated wake time


class StopRequest(BaseModel):
    project_id: UUID
    pause: bool = True  # True = docker pause (keep memory), False = docker stop


class HotReloadRequest(BaseModel):
    project_id: UUID
    # files: dict path → content. Same shape as `<file path="...">...</file>` extraction
    # from apps/api/src/omnia_api/services/file_extractor.py.
    files: dict[str, str]


class StatusResponse(BaseModel):
    project_id: UUID
    state: RuntimeState
    container_name: str | None = None
    port: int | None = None
    dev_url: str | None = None
    last_activity_at: str | None = None  # ISO8601
    cpu_pct: float | None = None
    memory_mb: int | None = None
    logs_tail_url: str | None = None  # signed url for last 200 lines
    # Area C (DARK): {email, auth_secret} for the gate's seed operator account,
    # populated ONLY when the orchestrator runs with OMNIA_GATE_SEED=1. Null on
    # every normal deployment, so the public contract is unchanged.
    gate_seed: dict | None = None


class DeployTargetCreds(BaseModel):
    """BYO-VPS: расшифрованные SSH-креды чужого сервера (от apps/api)."""

    host: str
    port: int = 22
    user: str
    auth_type: str
    secret: str


class DeployRequest(BaseModel):
    project_id: UUID
    # Optional: we deploy the live container state, not a git commit (runtime
    # has no git history — hot-reload writes files straight into the container).
    # Kept for forward-compat (future rollback-by-sha).
    commit_sha: str | None = None
    # BYO-VPS: если задан — деплоим собранный образ на этот чужой VPS по SSH,
    # а не на наш хост. None = наш хостинг.
    target: DeployTargetCreds | None = None
    # Подключённые к проекту домены — при деплое на свой VPS агент сам поднимает
    # edge (Caddy, авто-HTTPS) для них на машине пользователя.
    domains: list[str] | None = None


# Phases match apps/api DeployStatus so the api forwards them unchanged.
DeployPhase = Literal["queued", "building", "swapping", "done", "failed"]


class DeployResponse(BaseModel):
    project_id: UUID
    phase: DeployPhase
    prod_url: str | None = None
    image_tag: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class LogsResponse(BaseModel):
    project_id: UUID
    container_name: str | None = None
    tail: int
    logs: str  # raw stdout+stderr concatenated, UTF-8, newline-separated


class CompileStatusResponse(BaseModel):
    project_id: UUID
    # True = dev server is compiling cleanly (or no outstanding error); False =
    # the AI-written code currently fails to compile / errors at render.
    ok: bool
    error: str | None = None  # compact, ANSI-stripped excerpt of the error block
    file: str | None = None  # first implicated project source file (e.g. src/app/page.tsx)


class RuntimeStatusResponse(BaseModel):
    project_id: UUID
    # True = the running app served its route without a server error (or there's
    # nothing to probe); False = the rendered route returned 5xx.
    ok: bool
    status_code: int | None = None  # HTTP status the probe observed (None = no response)
    error: str | None = None  # parsed Next.js error excerpt from the dev logs
    file: str | None = None  # first implicated project source file
