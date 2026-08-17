from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnia_api.services import integration_providers


class _FakeClient:
    def __init__(self, response: httpx.Response, capture: dict[str, Any], **_kwargs: Any):
        self.response = response
        self.capture = capture

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.capture.update({"method": "GET", "url": url, **kwargs})
        return self.response


def test_aitunnel_provider_is_available_in_catalog() -> None:
    provider = integration_providers.get_provider("aitunnel")

    assert provider.name == "AITUNNEL"
    assert provider.category == "ai"
    assert provider.available is True
    assert provider.recommended is True
    assert provider.docs_url == "https://docs.aitunnel.ru/"
    assert [field.key for field in provider.fields] == ["api_key"]


@pytest.mark.asyncio
async def test_aitunnel_key_is_verified_by_read_only_profile_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = integration_providers.get_provider("aitunnel")
    public, secret = integration_providers.split_values(
        provider,
        {"api_key": "sk-aitunnel-test-value"},
    )
    capture: dict[str, Any] = {}
    response = httpx.Response(200, json={"id": 42, "email": "owner@example.test"})
    monkeypatch.setattr(
        integration_providers.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response, capture, **kwargs),
    )

    label = await integration_providers.verify_provider("aitunnel", public, secret)

    assert label == "AITUNNEL · owner@example.test"
    assert capture["method"] == "GET"
    assert capture["url"] == "https://api.aitunnel.ru/v1/aitunnel/me"
    assert capture["headers"]["Authorization"] == "Bearer sk-aitunnel-test-value"
