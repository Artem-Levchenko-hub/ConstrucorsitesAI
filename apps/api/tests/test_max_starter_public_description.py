"""A private generation instruction is not a public product description."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.generation.agent_seed import render_current_max_starter


@pytest.mark.parametrize("saved", [False, True])
async def test_starter_keeps_private_brief_out_of_public_config(saved):
    project_id = uuid4()
    config = MaxProjectConfigPayload.default_for("Кофейня")
    config.summary = "Публичное описание владельца"
    record = SimpleNamespace(config=config.model_dump(mode="json")) if saved else None

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(get=AsyncMock(return_value=record))

    private_brief = "PRIVATE_BRIEF_MARKER: добавь каталог и не публикуй это задание"
    files = await render_current_max_starter(
        factory=factory,
        ids=SimpleNamespace(project_id=project_id),
        project_info=SimpleNamespace(name="Кофейня"),
        prompt_text=private_brief,
        runtime=SimpleNamespace(handle=None),
    )
    source = "\n".join(files.values())
    assert "PRIVATE_BRIEF_MARKER" not in source
    assert ("Публичное описание владельца" if saved else
            "Мини-приложение для пользователей MAX") in source
