"""Explicit palette choices, separate from legacy inferred project presets.

The existing project field remains API-compatible. A per-request, bounded
preference records only an actual owner selection; no model call or new cursor
format is involved. Prepared prompts already persist the rendered guidance.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.design_presets import PRESETS

if TYPE_CHECKING:
    from yleum_api.services.generation.contracts import GenerationIds

_KEY = "owner_palette_v1"


def record_owner_palette(run: GenerationRun, preset_id: str) -> None:
    """Record a validated explicit selection in the admission transaction."""
    if preset_id not in PRESETS:
        return
    preference = {"version": 1, "preset_id": preset_id, "prompt_hash": run.prompt_hash}
    state = run.agent_state or {}
    if _KEY in state and state[_KEY] != preference:
        raise ValueError("explicit palette preference changed within one request")
    run.agent_state = {**state, _KEY: preference}


def owner_palette_context(run: GenerationRun, ids: GenerationIds) -> str:
    """Render only catalogued colors bound to this owner, request and message."""
    if (run.id, run.project_id, run.user_id, run.user_message_id) != (
        ids.run_id, ids.project_id, ids.user_id, ids.user_message_id,
    ):
        return ""
    value = (run.agent_state or {}).get(_KEY)
    if (
        type(value) is not dict
        or set(value) != {"version", "preset_id", "prompt_hash"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["preset_id"]) is not str or value["preset_id"] not in PRESETS
        or value["prompt_hash"] != run.prompt_hash
    ):
        return ""
    palette = PRESETS[value["preset_id"]].palette
    return (
        "\n\nВладелец явно выбрал палитру в текущем запросе: "
        + json.dumps(palette, ensure_ascii=False, sort_keys=True)
        + ". Это выбор цветов, не архитектуры, шрифтов или набора экранов. "
        "Прямые значения и ограничения в тексте запроса владельца имеют приоритет. "
        "Применяй выбор только в запрошенной области изменений; сохрани остальной интерфейс."
    )


async def load_owner_palette_context(
    factory: async_sessionmaker[AsyncSession], ids: GenerationIds,
) -> str:
    async with factory() as session:
        run = await session.get(GenerationRun, ids.run_id)
    return owner_palette_context(run, ids) if run is not None else ""
