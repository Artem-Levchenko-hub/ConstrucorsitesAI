"""Request schema for the direct style-patch endpoint (in-preview color/font edit).

Light validation here (shape + lengths + hex form → 422). Semantic rejects
(banned brand-AI hexes, unknown font family) happen in the router so they surface
as explicit 400s with stable error codes.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_VAR_RE = re.compile(r"^--[a-z0-9-]{1,40}$")


def _check_hex(v: str | None) -> str | None:
    if v is None:
        return None
    if not _HEX_RE.match(v):
        raise ValueError("must be a #rgb or #rrggbb hex color")
    return v


class TokenOverride(BaseModel):
    """A site-wide brand-token change, e.g. var='--accent' value='#E11D48'."""

    var: str = Field(min_length=3, max_length=42)
    value: str = Field(min_length=4, max_length=7)

    @field_validator("var")
    @classmethod
    def _var_shape(cls, v: str) -> str:
        if not _VAR_RE.match(v):
            raise ValueError("var must look like --token-name")
        return v

    @field_validator("value")
    @classmethod
    def _value_hex(cls, v: str) -> str:
        return _check_hex(v)  # type: ignore[return-value]


class ElementOverride(BaseModel):
    """A single element's color/font change, keyed by the inspector CSS selector."""

    selector: str = Field(min_length=1, max_length=600)
    color: str | None = None
    background_color: str | None = None
    border_color: str | None = None
    font_family: str | None = Field(default=None, max_length=80)

    @field_validator("color", "background_color", "border_color")
    @classmethod
    def _colors_hex(cls, v: str | None) -> str | None:
        return _check_hex(v)


class StylePatchRequest(BaseModel):
    tokens: list[TokenOverride] = Field(default_factory=list, max_length=24)
    elements: list[ElementOverride] = Field(default_factory=list, max_length=60)
