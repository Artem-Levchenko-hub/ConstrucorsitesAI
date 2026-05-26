from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from omnia_api.services.design_presets import PRESETS

# Template values fall into two classes:
#
# 1. Static V1 (`blank/landing/portfolio/blog`) — pure HTML rendered out
#    of the project's bare git via `/p/<slug>`. No orchestrator container.
#
# 2. Container-backed V2 — runs inside an orchestrator-provisioned Docker
#    container, accessed via `runtime.dev_url`:
#    * `fullstack`  → `nextjs-postgres-drizzle` (Next.js 15 + Postgres + auth)
#    * `spa`        → `vite-react-spa`           (Vite + React, no backend)
#    * `tgbot`      → `telegram-bot-aiogram`     (aiogram 3 + Postgres)
#    * `api`        → `fastapi-postgres`         (FastAPI + SQLAlchemy + JWT)
#
# All container-backed templates share the `<file path="...">` AI contract
# and the same per-project Postgres schema (provisioned by
# `orchestrator.postgres_admin`).
Template = Literal[
    "blank",
    "landing",
    "portfolio",
    "blog",
    "fullstack",
    "spa",
    "tgbot",
    "api",
]

# Map from API-side `template` value → orchestrator's template directory
# name (which becomes `omnia-template-<name>:dev` image tag and the
# `templates/<name>/` source dir). Single source of truth — runtime.py
# imports this rather than hardcoding.
_ORCHESTRATOR_TEMPLATE_BY_API: dict[str, str] = {
    "fullstack": "nextjs-postgres-drizzle",
    "spa": "vite-react-spa",
    "tgbot": "telegram-bot-aiogram",
    "api": "fastapi-postgres",
}


def is_fullstack(template: str) -> bool:
    """Single source of truth: which templates run inside a dev container.

    Renamed scope: still called "fullstack" for backward-compat with
    existing api-internal calls, but now means "container-backed" (any of
    fullstack/spa/tgbot/api). Static templates still return False.
    """
    return template in _ORCHESTRATOR_TEMPLATE_BY_API


def orchestrator_template(template: str) -> str | None:
    """Map an API-side template value to its orchestrator directory name.

    Returns None for static templates (blank/landing/portfolio/blog) —
    those don't have a Docker image, the caller (`routers/runtime.py`)
    treats None as "no container needed; this stays on /p/<slug>".
    """
    return _ORCHESTRATOR_TEMPLATE_BY_API.get(template)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template: Template = "blank"


class ProjectUpdate(BaseModel):
    """Partial update — fields the owner can toggle from the workspace.

    Only `image_gen_enabled` is exposed for now (TopBar 🎨 toggle). Adding more
    fields here later (e.g. rename) is just a matter of declaring them
    Optional and applying them in the PATCH handler.
    """

    image_gen_enabled: bool | None = None


class ProjectPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    slug: str
    template: Template
    design_preset_id: str | None = None
    image_gen_enabled: bool = True
    current_snapshot_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def design_preset_name(self) -> str | None:
        """Human-readable preset name derived from id (for read-only UI badge)."""
        if self.design_preset_id and (preset := PRESETS.get(self.design_preset_id)):
            return preset.name
        return None
