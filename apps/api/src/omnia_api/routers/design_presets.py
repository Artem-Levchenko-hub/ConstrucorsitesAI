"""Public API for the 8 design presets the generator can ride on.

Two endpoints:

- ``GET /api/design-presets`` — catalog the frontend renders as a picker
  (swatch + font-pair + one-liner + reference URL). The full preset
  payload (layout_signatures, kit_classes, copywriting_examples,
  anti_patterns) stays server-side — it's prompt-builder fuel, not UI
  content.

- ``PUT /api/projects/<id>/design-preset`` — manual override. Auto-classify
  runs on the first prompt (`preset_classifier.classify_preset`) and writes
  the project's ``design_preset_id``; this endpoint lets the user pick a
  different one before generating, or switch presets mid-project. Subsequent
  prompts pick up the new preset on the next ``build_messages`` call.

Why no GET for a single project's preset: the project itself already exposes
``design_preset_id`` in its response (see ``projects_router``); a dedicated
endpoint would just duplicate that field.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.services.design_presets import PRESETS

router = APIRouter(prefix="/api", tags=["design-presets"])


class DesignPresetPublic(BaseModel):
    """Frontend-facing view. Strips prompt-builder internals (layout_signatures,
    kit_classes, copywriting_examples, anti_patterns) — those are server-only
    fuel for the LLM; the picker just needs swatches + name + reference."""

    id: str
    name: str
    one_liner: str
    reference_url: str
    palette: dict[str, str]
    fonts: dict[str, str]
    hero_type: str
    industries: list[str]


def _to_public(preset_id: str) -> DesignPresetPublic:
    p = PRESETS[preset_id]
    return DesignPresetPublic(
        id=p.id,
        name=p.name,
        one_liner=p.one_liner,
        reference_url=p.reference_url,
        palette=dict(p.palette),
        fonts={k: v for k, v in p.fonts.items() if k != "google_fonts_url"},
        hero_type=p.hero_type,
        industries=list(p.industries),
    )


@router.get("/design-presets", response_model=list[DesignPresetPublic])
async def list_design_presets() -> list[DesignPresetPublic]:
    """List the 8 Awwwards-tier presets the generator can target.

    Public surface — no auth, no per-user filtering. Catalog is static
    (declared in services/design_presets.py) so we don't bother with caching.
    """
    return [_to_public(pid) for pid in PRESETS]


class SetDesignPresetRequest(BaseModel):
    """`preset_id=None` unsets the override and lets auto-classify re-run on
    the next prompt. A non-null value MUST match one of the 8 known ids —
    anything else is a 400 (the picker only sends what we listed)."""

    preset_id: str | None = Field(default=None)


class SetDesignPresetResponse(BaseModel):
    preset_id: str | None


@router.put(
    "/projects/{project_id}/design-preset",
    response_model=SetDesignPresetResponse,
)
async def set_project_design_preset(
    project_id: UUID,
    payload: SetDesignPresetRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SetDesignPresetResponse:
    """Set or clear the project's design preset override.

    Effective on the NEXT prompt — already-generated snapshots keep whichever
    preset was used when they ran. Idempotent: same value twice is a no-op.
    """
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    if payload.preset_id is not None and payload.preset_id not in PRESETS:
        raise ApiError(
            "invalid_preset",
            f"unknown preset_id: {payload.preset_id!r}",
            status.HTTP_400_BAD_REQUEST,
        )

    project.design_preset_id = payload.preset_id
    await session.commit()
    return SetDesignPresetResponse(preset_id=project.design_preset_id)
