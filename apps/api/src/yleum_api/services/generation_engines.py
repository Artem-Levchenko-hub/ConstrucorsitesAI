"""Internal QA engine selection; accepted dispatches retain their own model."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.user import User
from yleum_api.services.agent_output_budget import SOL_MODEL as SOL_MODEL
from yleum_api.services.entitlements import has_qa_unlimited_generations, load_plan_context

GenerationEngine = Literal["claude", "sol"]
ENGINE_SELECTION_KEY = "qa_generation_engine_v1"


async def can_choose_generation_engine(session: AsyncSession, user: User) -> bool:
    # The private active QA plan is authority, not an email or a browser flag.
    if user.role != "admin":
        return False
    return has_qa_unlimited_generations(user, await load_plan_context(session, user.id))


def selected_engine(state: object) -> GenerationEngine:
    if not isinstance(state, dict) or ENGINE_SELECTION_KEY not in state:
        return "claude"
    if state[ENGINE_SELECTION_KEY] != {"version": 1, "engine": "sol", "model": SOL_MODEL}:
        raise ValueError("Generation engine selection is invalid")
    return "sol"


def engine_selection(engine: GenerationEngine) -> dict[str, object]:
    return (
        {ENGINE_SELECTION_KEY: {"version": 1, "engine": "sol", "model": SOL_MODEL}}
        if engine == "sol"
        else {}
    )
