import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from omnia_api.services.design_presets import PRESETS

# Template values fall into two classes:
#
# 1. Static V1 (`blank/landing/portfolio/blog`) — pure HTML rendered out
#    of the project's bare git via `/p/<slug>`. No orchestrator container.
#
# 2. Container-backed V2 — runs inside an orchestrator-provisioned Docker
#    container, accessed via `runtime.dev_url`:
#    * `fullstack`      → `nextjs-postgres-drizzle` (Next.js 15 + Postgres + auth)
#    * `nextjs_entities`→ `nextjs-entities`         (Base44-style: fixed entity-engine
#                                                    backend + generative React frontend)
#    * `spa`            → `vite-react-spa`           (Vite + React, no backend)
#    * `tgbot`          → `telegram-bot-aiogram`     (aiogram 3 + Postgres)
#    * `api`            → `fastapi-postgres`         (FastAPI + SQLAlchemy + JWT)
#
# All container-backed templates share the `<file path="...">` AI contract
# and the same per-project Postgres schema (provisioned by
# `orchestrator.postgres_admin`).
# `code` (added 2026-06-18) — language-agnostic source (Python script, Go CLI,
# a parser, anything). Like the static class it has NO orchestrator container
# (intentionally omitted from `_ORCHESTRATOR_TEMPLATE_BY_API` below, so
# `is_fullstack`/`orchestrator_template` treat it as non-container). The model
# writes arbitrary files via the same `<file path="...">` contract; we store,
# version and let the user download / GitHub-push them — we never force a website
# or "preview" them as one (owner directive: don't lock the builder to a language).
Template = Literal[
    "blank",
    "landing",
    "portfolio",
    "blog",
    "fullstack",
    "nextjs_entities",
    "spa",
    "tgbot",
    "api",
    "code",
]

# Map from API-side `template` value → orchestrator's template directory
# name (which becomes `omnia-template-<name>:dev` image tag and the
# `templates/<name>/` source dir). Single source of truth — runtime.py
# imports this rather than hardcoding.
_ORCHESTRATOR_TEMPLATE_BY_API: dict[str, str] = {
    "fullstack": "nextjs-postgres-drizzle",
    "nextjs_entities": "nextjs-entities",
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
    language: str | None = None


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
    language: str = "ru"
    design_preset_id: str | None = None
    image_gen_enabled: bool = True
    # Import provenance (migration 0019). "native" for all organic projects;
    # "imported" when seeded from an external GitHub repo.
    source: str = "native"
    external_repo_url: str | None = None
    # Lineage for V4.1b "Remix this": the project this one was forked from, or
    # None for organically created projects. Lets the client show provenance and
    # a "back to original" / attribution edge (the viral return-loop, V4.2b).
    forked_from: UUID | None = None
    # Resolved at read time (get_project) from `forked_from` so the workspace can
    # show WHICH project this is a remix of and link to it — the transitive remix
    # lineage (V4 #3). Not mapped columns: the projects router sets them on the
    # ORM instance, same as `preview_url`. Stay None for organic projects (and
    # when the source has been deleted).
    forked_from_name: str | None = None
    forked_from_slug: str | None = None
    current_snapshot_id: UUID | None
    # Thumbnail of the current snapshot (its rendered preview PNG), or None until
    # the first preview render lands. Not a mapped column — the projects router
    # sets it from the current snapshot's preview_key.
    preview_url: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def design_preset_name(self) -> str | None:
        """Human-readable preset name derived from id (for read-only UI badge)."""
        if self.design_preset_id and (preset := PRESETS.get(self.design_preset_id)):
            return preset.name
        return None


# ---------------------------------------------------------------------------
# Import request (B2 — GitHub repo import)
# ---------------------------------------------------------------------------

_GH_SHORTHAND_RE = re.compile(
    r"^(?:https?://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class ProjectImportRequest(BaseModel):
    """Request body for POST /projects/import.

    `repo_url` accepts either a full GitHub URL
    (``https://github.com/owner/repo``) or the shorthand ``owner/repo`` form.
    `ref` is an optional branch/tag/SHA; defaults to the repo's default branch.
    `name` overrides the generated project name; defaults to the repo name.
    """

    repo_url: str
    ref: str | None = None
    name: str | None = None

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v: str) -> str:
        if not _GH_SHORTHAND_RE.match(v.strip().removesuffix(".git").rstrip("/")):
            raise ValueError(
                "repo_url must be a github.com URL or 'owner/repo' shorthand"
            )
        return v
