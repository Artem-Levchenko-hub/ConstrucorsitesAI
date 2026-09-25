"""«Готово» должно означать, что данные приложения читаются, а не что сборка зелёная.

LIVE-04: текущая схема объявляла девять служебных таблиц, в свежей базе была только
QA-миграция, подписанный GET бизнес-маршрута падал с 500 (`42P01`, таблицы нет) — а
сборка и проверка типов при этом были зелёными, и запуск объявлялся успешным.

Эти тесты закрепляют отдельное доказательство: подписанное чтение реального
маршрута данных. Пустой ответ — валиден, 500 — нет, и никакое количество зелёных
сборок его не заменяет.
"""

from __future__ import annotations

import httpx
import pytest

from yleum_api.services.max_runtime_probe import probe_signed_business_endpoint

_ORIGIN = "https://cell-canary-dev.preview.example"


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_ORIGIN,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        follow_redirects=False,
    )


async def test_missing_max_users_prevents_completed_despite_green_build() -> None:
    # Exactly LIVE-04: the route exists, the table behind it does not.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"error": 'relation "max_users" does not exist', "code": "42P01"}
        )

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(client, "/api/qa-clients")

    assert result["ok"] is False
    assert result["reason"] == "signed_business_route_failed"
    assert result["status"] == 500
    # The failure text belongs to the operator's evidence, not to the agent's context.
    assert "max_users" not in str(result)


async def test_signed_empty_business_route_is_valid() -> None:
    # An application with no rows yet is a correct application.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(client, "/api/qa-clients")

    assert result["ok"] is True and result["reason"] == "ok"
    assert result["status"] == 200


async def test_business_route_500_does_not_settle() -> None:
    for status in (500, 502, 503):
        def handler(request: httpx.Request, code: int = status) -> httpx.Response:
            return httpx.Response(code, text="boom")

        async with _client(handler) as client:
            result = await probe_signed_business_endpoint(client, "/api/qa-clients")

        assert result["ok"] is False, status
        assert result["reason"] == "signed_business_route_failed"


async def test_an_unauthenticated_answer_is_not_a_business_proof() -> None:
    # The session is supposed to be signed; 401 means the proof never happened.
    for status in (401, 403):
        def handler(request: httpx.Request, code: int = status) -> httpx.Response:
            return httpx.Response(code, json={"detail": "auth required"})

        async with _client(handler) as client:
            result = await probe_signed_business_endpoint(client, "/api/qa-clients")

        assert result["ok"] is False
        assert result["reason"] == "signed_business_route_unauthorized"


async def test_a_redirect_is_never_accepted_as_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/signin"})

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(client, "/api/qa-clients")

    assert result["ok"] is False
    assert result["reason"] == "signed_business_route_redirected"


@pytest.mark.parametrize(
    "path",
    ["/api/omnia/health", "/api/max/webhook", "relative", "//evil.example/api/x", "/api/x?q=1"],
)
async def test_proof_refuses_a_route_that_is_not_the_products_own(path: str) -> None:
    # Platform-managed endpoints and anything that can leave the origin are not
    # evidence that the generated product serves its own data.
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("the probe must refuse before sending a request")

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(client, path)

    assert result["ok"] is False
    assert result["reason"] == "signed_business_route_invalid"
    assert result["status"] is None


async def test_proof_refuses_foreign_preview_binding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(
            client, "/api/qa-clients", expected_origin="https://someone-else.example"
        )

    assert result["ok"] is False
    assert result["reason"] == "signed_business_route_foreign_binding"


async def test_the_shape_digest_describes_the_answer_without_carrying_it() -> None:
    secret_value = "Иван Синтетический +79990010921"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "1", "name": secret_value, "note": None}])

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(client, "/api/qa-clients")

    assert result["ok"] is True
    rendered = str(result)
    assert secret_value not in rendered and "79990010921" not in rendered
    assert len(str(result["shape_digest"])) == 64


async def test_the_same_shape_with_different_values_digests_the_same() -> None:
    def make(name: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"id": "1", "name": name}])

        return handler

    async with _client(make("Первый")) as client:
        first = await probe_signed_business_endpoint(client, "/api/qa-clients")
    async with _client(make("Второй")) as client:
        second = await probe_signed_business_endpoint(client, "/api/qa-clients")

    assert first["shape_digest"] == second["shape_digest"]


async def test_a_transport_failure_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with _client(handler) as client:
        result = await probe_signed_business_endpoint(client, "/api/qa-clients")

    assert result["ok"] is False
    assert result["reason"] == "signed_business_route_unreachable"
    assert result["status"] is None
    assert "no route to host" not in str(result)


async def test_the_cell_probe_refuses_a_green_page_with_a_broken_data_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of LIVE-04: the page renders, the data route is 500."""
    from types import SimpleNamespace

    from yleum_api.services import max_runtime_probe as probe_module

    async def signed_runtime(*args: object, **kwargs: object) -> probe_module.MaxRuntimeProbe:
        return probe_module.MaxRuntimeProbe(True, "product page served")

    async def business(client: object, path: str, *a: object, **k: object) -> dict[str, object]:
        return {
            "ok": False,
            "status": 500,
            "reason": "signed_business_route_failed",
            "shape_digest": None,
        }

    monkeypatch.setattr(probe_module, "_probe_signed_runtime", signed_runtime)
    monkeypatch.setattr(probe_module, "probe_signed_business_endpoint", business)
    preview = SimpleNamespace(bootstrap_url=f"{_ORIGIN}/api/omnia/preview-session?s=x")

    result = await probe_module.probe_max_cell_runtime(
        preview,  # type: ignore[arg-type]
        business_path="/api/qa-clients",
        proof_key="a" * 64,
    )

    assert result.ok is False
    assert "signed_business_route_failed" in result.detail
    assert result.artifact_digest is None


async def test_without_a_contract_route_the_probe_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from yleum_api.services import max_runtime_probe as probe_module

    async def signed_runtime(*args: object, **kwargs: object) -> probe_module.MaxRuntimeProbe:
        return probe_module.MaxRuntimeProbe(True, "product page served")

    async def business(*a: object, **k: object) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("no contract route means no business probe")

    monkeypatch.setattr(probe_module, "_probe_signed_runtime", signed_runtime)
    monkeypatch.setattr(probe_module, "probe_signed_business_endpoint", business)
    preview = SimpleNamespace(bootstrap_url=f"{_ORIGIN}/api/omnia/preview-session?s=x")

    result = await probe_module.probe_max_cell_runtime(preview, proof_key="a" * 64)  # type: ignore[arg-type]

    assert result.ok is True and result.artifact_digest is not None
