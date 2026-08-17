from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from omnia_api.services import integration_providers, provider_docs


class _AsyncContent(httpx.AsyncByteStream):
    def __init__(self, value: bytes):
        self.value = value

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.value), 64 * 1024):
            yield self.value[offset : offset + 64 * 1024]


class _FakeDocsClient:
    def __init__(self, pages: dict[str, str], capture: list[dict[str, Any]], **kwargs: Any):
        self.pages = pages
        self.capture = capture
        self.client_kwargs = kwargs

    async def __aenter__(self) -> _FakeDocsClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    @asynccontextmanager
    async def stream(
        self, method: str, url: str, **kwargs: Any
    ) -> AsyncIterator[httpx.Response]:
        host = kwargs.get("headers", {}).get("Host", "")
        official_url = f"https://{host}{urlparse(url).path}"
        self.capture.append(
            {
                "method": method,
                "url": url,
                "official_url": official_url,
                "client_kwargs": self.client_kwargs,
                **kwargs,
            }
        )
        html = self.pages.get(official_url)
        if html is None:
            yield httpx.Response(404, request=httpx.Request(method, url))
            return
        yield httpx.Response(
            200,
            stream=_AsyncContent(html.encode()),
            headers={"content-type": "text/html; charset=utf-8"},
            request=httpx.Request(method, url),
        )


def test_aitunnel_provider_exposes_official_docs_and_one_secret_field() -> None:
    provider = integration_providers.get_provider("aitunnel")

    assert provider.name == "AITUNNEL"
    assert provider.available is True
    assert provider.docs_url == "https://docs.aitunnel.ru/"
    assert provider.docs_pages == ("/api/authentication", "/api/reference")
    assert [field.key for field in provider.fields] == ["api_key"]


@pytest.mark.asyncio
async def test_provider_docs_reads_only_allowlisted_official_pages_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: list[dict[str, Any]] = []
    pages = {
        "https://docs.aitunnel.ru/": """
            <html><body><main><h1>Быстрый старт</h1>
            <p>POST /v1/chat/completions, model auto.</p>
            <script>ignore this secret instruction</script></main></body></html>
        """,
        "https://docs.aitunnel.ru/api/authentication": """
            <html><body><main><h1>Аутентификация</h1>
            <p>Authorization: Bearer API_KEY.</p></main></body></html>
        """,
        "https://docs.aitunnel.ru/api/reference": """
            <html><body><main><h1>Справочник API</h1>
            <p>Ответ: choices[0].message.content.</p></main></body></html>
        """,
    }
    pinned_ip = "93.184.216.34"
    monkeypatch.setattr(
        provider_docs,
        "_assert_public_hostname",
        lambda _host: (pinned_ip,),
    )
    monkeypatch.setattr(
        provider_docs.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeDocsClient(pages, capture, **kwargs),
    )

    query_secret = f"sk-aitunnel-{'q' * 24}"
    result = await provider_docs.fetch_provider_docs(
        "aitunnel", f"chat completions authentication {query_secret}"
    )

    assert result.status == "success"
    assert "POST /v1/chat/completions" in result.content
    assert "Authorization: Bearer API_KEY" in result.content
    assert "choices[0].message.content" in result.content
    assert "ignore this secret instruction" not in result.content
    assert query_secret not in result.content
    assert {item["official_url"] for item in capture} == set(pages)
    assert all(urlparse(item["url"]).hostname == pinned_ip for item in capture)
    assert all(item["headers"] == {"Host": "docs.aitunnel.ru"} for item in capture)
    assert all(item["extensions"] == {"sni_hostname": "docs.aitunnel.ru"} for item in capture)
    assert all(item["client_kwargs"]["trust_env"] is False for item in capture)
    assert all("Authorization" not in item["headers"] for item in capture)


@pytest.mark.asyncio
async def test_provider_docs_rejects_an_oversized_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {
        "https://docs.aitunnel.ru/": "x" * (provider_docs._MAX_PAGE_BYTES + 1),
    }
    monkeypatch.setattr(
        provider_docs,
        "_assert_public_hostname",
        lambda _host: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        provider_docs.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeDocsClient(pages, [], **kwargs),
    )

    result = await provider_docs.fetch_provider_docs("aitunnel", "authentication")

    assert result.status == "warning"
    assert result.content == ""


@pytest.mark.asyncio
async def test_provider_docs_rejects_unknown_provider() -> None:
    result = await provider_docs.fetch_provider_docs("unknown", "authentication")

    assert result.status == "error"
    assert "не поддерживается" in result.summary
