from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.generation.agent_preparation import _seed_saved_max_config
from yleum_api.services.max_managed_generation import load_saved_business_config


def context(record=True):
    ids = SimpleNamespace(project_id=uuid4(), user_id=uuid4())
    raw = MaxProjectConfigPayload.default_for("Saved owner brief").model_dump(mode="json")
    raw["content"] = [{"id": "stable-id", "title": "Услуга", "price": "По запросу"}]
    saved = SimpleNamespace(owner_id=ids.user_id, config_version=7, config=raw) if record else None

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, model, key):
            assert model is MaxProjectConfig and key == ids.project_id
            return saved

    return ids, saved, Session


async def test_canonical_catalog_seed_preserves_owner_ids_prices_and_version():
    ids, _, factory = context()
    config, version = await load_saved_business_config(
        factory, project_id=ids.project_id, owner_id=ids.user_id,
    )
    assert version == 7
    assert config.content[0].id == "stable-id"
    assert config.content[0].price == "По запросу"


async def test_missing_catalog_is_empty_version_zero_and_non_max_is_unchanged():
    ids, _, factory = context(False)
    assert await load_saved_business_config(
        factory, project_id=ids.project_id, owner_id=ids.user_id,
    ) == (None, 0)
    config, version = await load_saved_business_config(
        factory, project_id=ids.project_id, owner_id=ids.user_id, default_name="New MAX",
    )
    assert version == 0 and config.content == [] and config.app_name == "New MAX"


async def test_seed_rejects_another_owner():
    ids, saved, factory = context()
    saved.owner_id = uuid4()
    with pytest.raises(ApiError) as caught:
        await load_saved_business_config(factory, project_id=ids.project_id, owner_id=ids.user_id)
    assert caught.value.status_code == 409


@pytest.mark.parametrize("label,expected", [
    ("я" * 120, "я" * 100), ("   ", "Мини-приложение"),
])
async def test_unsaved_project_label_fits_the_real_app_name_contract(label, expected):
    ids, _, factory = context(False)
    config, version = await load_saved_business_config(
        factory, project_id=ids.project_id, owner_id=ids.user_id, default_name=label,
    )
    assert version == 0 and config.app_name == expected and config.content == []


@pytest.mark.parametrize("missing_handle", [False, True])
async def test_max_seed_requires_the_real_runtime_channel(missing_handle):
    ids, _, factory = context()
    runtime = SimpleNamespace(handle=None if missing_handle else SimpleNamespace())
    with pytest.raises(ApiError) as caught:
        await _seed_saved_max_config(factory, ids, SimpleNamespace(name="Brief"), runtime,
                                     catalog_runtime=True)
    assert caught.value.status_code == 503


async def test_canonical_runtime_seed_precedes_source_and_failed_seed_stops_delivery(monkeypatch):
    ids, _, factory = context()
    events = []
    async def seed(version, payload):
        events.append("runtime")
        assert version == 7 and payload["content"][0]["id"] == "stable-id"
        assert "max_url_attached" not in payload
    def render(config, project_id):
        assert events == ["runtime"] and project_id == ids.project_id
        events.append("source")
        return {"src/lib/omnia/max-config.ts": "canonical config"}
    from yleum_api.services import max_project_kit
    monkeypatch.setattr(max_project_kit, "render_max_managed_files", render)
    handle = SimpleNamespace(seed_business_config=seed, snapshot_files=AsyncMock(return_value={}))
    runtime = SimpleNamespace(handle=handle)
    assert await _seed_saved_max_config(factory, ids, SimpleNamespace(name="Brief"), runtime,
                                       catalog_runtime=True) == "canonical config"
    assert events == ["runtime", "source"]
    events.clear()
    handle.seed_business_config = AsyncMock(side_effect=RuntimeError("seed unavailable"))
    with pytest.raises(RuntimeError, match="seed unavailable"):
        await _seed_saved_max_config(factory, ids, SimpleNamespace(name="Brief"), runtime,
                                     catalog_runtime=True)
    assert events == []
