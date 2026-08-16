"""Strict, transport-safe product contract for a generated MAX Mini App."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyShortText = Annotated[str, Field(min_length=1, max_length=240)]
NonEmptyAudience = Annotated[str, Field(min_length=1, max_length=400)]
NonEmptyScreen = Annotated[str, Field(min_length=1, max_length=120)]
NonEmptyCriterion = Annotated[str, Field(min_length=1, max_length=300)]
MaxPrimaryActionKind = Literal["local_navigation", "managed_write", "catalog_read"]


class MaxProductSpec(BaseModel):
    """Deterministic business contract collected by MAX Studio.

    This is deliberately separate from the free-form chat prompt: workers can
    persist and replay the same bounded contract without parsing prose again.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    purpose: str = Field(min_length=1, max_length=800)
    audience: NonEmptyAudience
    screens: list[NonEmptyScreen] = Field(min_length=2, max_length=8)
    primary_action: NonEmptyShortText
    # Closed execution contract. Never infer this from the free-form action label.
    primary_action_kind: MaxPrimaryActionKind
    capabilities: list[NonEmptyShortText] = Field(max_length=8)
    data: list[NonEmptyShortText] = Field(min_length=1, max_length=8)
    # True means the primary flow creates user-owned state and therefore needs
    # managed write + reload restoration proof. A separate history screen is
    # represented independently in ``screens``.
    history: bool
    integrations: list[NonEmptyShortText] = Field(min_length=1, max_length=6)
    style: NonEmptyShortText
    acceptance: list[NonEmptyCriterion] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def _action_kind_matches_history(self) -> MaxProductSpec:
        requires_history = self.primary_action_kind == "managed_write"
        if self.history != requires_history:
            raise ValueError(
                "history must be true only for primary_action_kind=managed_write"
            )
        return self

    @field_validator("screens", "capabilities", "data", "integrations", "acceptance")
    @classmethod
    def _unique_normalized_items(cls, values: list[str]) -> list[str]:
        normalized = [value.casefold() for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("list items must be unique ignoring case and surrounding whitespace")
        return values
