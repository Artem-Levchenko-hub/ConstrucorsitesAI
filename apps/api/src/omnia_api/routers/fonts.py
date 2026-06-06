"""Public font catalog for the in-preview font picker.

``GET /api/fonts`` → the families the style editor can apply, each with its
Google Fonts stylesheet URL + a ready CSS stack. Public + static (like
``/api/design-presets``), so the frontend can cache it for the whole session.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from omnia_api.services.fonts import font_catalog

router = APIRouter(prefix="/api", tags=["fonts"])


class FontPublic(BaseModel):
    family: str
    category: str
    google_fonts_url: str
    css_stack: str


@router.get("/fonts", response_model=list[FontPublic])
async def list_fonts() -> list[FontPublic]:
    return [
        FontPublic(
            family=f.family,
            category=f.category,
            google_fonts_url=f.google_fonts_url,
            css_stack=f.css_stack,
        )
        for f in font_catalog()
    ]
