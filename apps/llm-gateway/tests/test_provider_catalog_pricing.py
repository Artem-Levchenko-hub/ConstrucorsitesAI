"""Provider quotes remain estimates; reported rubles always win."""
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_gateway.services import pricing


def catalog(input_price="349.9153", output_price="1749.5765", cached="34.9915"):
    return {"items": [{"model_name": "anthropic/claude-sonnet-5", "pricing": {
        "currency": "RUB", "unit": "1M_tokens", "input_rub_per_mtok": input_price,
        "output_rub_per_mtok": output_price, "cached_input_rub_per_mtok": cached,
        "rates": [{"billable": kind, "unit": "token", "quantity": "1000000.000000",
                   "variant": "", "price_rub": amount, "is_from_price": False}
                  for kind, amount in [("input_text", input_price), ("output_text", output_price),
                                       ("cached_input", cached)]]}}]}


@pytest.mark.parametrize("rub", ["0", "0.25"])
async def test_reported_rubles_win_without_catalog(monkeypatch, rub):
    fetch = AsyncMock(side_effect=AssertionError("receipt must not need network"))
    monkeypatch.setattr(pricing, "_fetch_catalog_prices", fetch)
    cost, evidence = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000,
        tokens_out=100, reported=pricing.ReportedCost(Decimal(rub), Decimal(".0025"),
        "header:x-llmgw-cost-rub", "usage:cost"))
    assert cost == Decimal(rub)
    assert evidence["basis"] == "provider_reported_rub"
    assert evidence["provider_cost_usd"] == "0.0025"
    fetch.assert_not_awaited()


async def test_catalog_uses_actual_cache_tokens_and_preserves_usd(monkeypatch):
    quote = pricing._parse_catalog_prices(catalog())
    monkeypatch.setattr(pricing, "_fetch_catalog_prices", AsyncMock(return_value=quote))
    cost, evidence = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000,
        tokens_out=100, cache_read_tokens=600,
        reported=pricing.ReportedCost(cost_usd=Decimal(".001"), usd_source="usage:cost"))
    assert cost == Decimal(".3359")
    assert evidence["basis"] == "provider_catalog_estimate"
    assert evidence["reported_cost_rub"] is None
    assert evidence["provider_cost_usd"] == "0.001"
    assert evidence["tariff"]["rub_per_1k_in"] == "0.3499153"
    assert pricing.validate_cost_provenance(evidence, cost_rub=cost) == evidence


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("unit", "1K_tokens"),
    ("input_rub_per_mtok", "NaN"), ("output_rub_per_mtok", "-1"),
    ("cached_input_rub_per_mtok", True), ("input_rub_per_mtok", "1e-999999")])
async def test_invalid_quote_falls_back(monkeypatch, field, value):
    data = catalog()
    data["items"][0]["pricing"][field] = value
    monkeypatch.setattr(pricing, "_fetch_catalog_prices",
        AsyncMock(return_value=pricing._parse_catalog_prices(data)))
    cost, evidence = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000,
        tokens_out=100)
    assert cost == Decimal(".4845")
    assert evidence["basis"] == "token_tariff"


@pytest.mark.parametrize("estimated,write", [(True, 0), (False, 200)])
async def test_no_invented_cache_write_or_token_receipt(monkeypatch, estimated, write):
    monkeypatch.setattr(pricing, "_fetch_catalog_prices",
        AsyncMock(return_value=pricing._parse_catalog_prices(catalog())))
    cost, evidence = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000,
        tokens_out=100, cache_write_tokens=write, estimated_tokens=estimated)
    assert cost == pricing.calculate_cost_rub("claude-sonnet-5", 1000, 100, 0, write)
    assert evidence["basis"] == ("local_token_estimate" if estimated else "token_tariff")


def test_tiered_prices_rejected():
    data = catalog()
    data["items"][0]["pricing"]["rates"][0]["variant"] = "long-context"
    assert pricing._parse_catalog_prices(data) == {}


@pytest.mark.parametrize("billable", [[], {}])
def test_malformed_billable_cannot_abort_paid_response(billable):
    data = catalog()
    data["items"][0]["pricing"]["rates"][0]["billable"] = billable
    assert pricing._parse_catalog_prices(data) == {}


async def test_catalog_http_is_fixed_public_bounded_no_auth(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=catalog())
    original = httpx.AsyncClient
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs:
        original(transport=httpx.MockTransport(handler), **kwargs))
    quotes = await pricing._fetch_catalog_prices()
    assert "claude-sonnet-5" in quotes
    assert str(requests[0].url) == pricing._CATALOG_URL + "?limit=200"
    assert "authorization" not in requests[0].headers


async def test_oversize_catalog_rejected(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs:
        original(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            content=b" " * (1024 * 1024 + 1))), **kwargs))
    assert await pricing._fetch_catalog_prices() == {}


async def test_gemini_catalog_uses_its_own_output_rate(monkeypatch):
    data = catalog(output_price="2099.4918")
    data["items"][0]["model_name"] = "google/gemini-3.1-pro-preview-customtools"
    monkeypatch.setattr(pricing, "_fetch_catalog_prices",
                        AsyncMock(return_value=pricing._parse_catalog_prices(data)))
    cost, evidence = await pricing.resolve_request_cost("gemini-3.1-pro-preview-customtools",
                                                       tokens_in=1000, tokens_out=100)
    assert cost == Decimal(".5599")
    assert evidence["tariff"]["rub_per_1k_out"] == "2.0994918"


async def test_catalog_expiry_refreshes_without_mutating_previous_receipt(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(pricing.time, "monotonic", lambda: clock[0])
    fetch = AsyncMock(side_effect=[pricing._parse_catalog_prices(catalog()),
        pricing._parse_catalog_prices(catalog(input_price="400", cached="40")), {}])
    monkeypatch.setattr(pricing, "_fetch_catalog_prices", fetch)
    first, evidence = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000, tokens_out=0)
    clock[0] = 1
    second, _ = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000, tokens_out=0)
    assert first == second == Decimal(".3499")
    assert fetch.await_count == 1
    clock[0] = 16
    third, _ = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000, tokens_out=0)
    assert third == Decimal(".4000")
    assert evidence["effective_cost_rub"] == "0.3499"
    clock[0] = 32
    fallback, snapshot = await pricing.resolve_request_cost("claude-sonnet-5", tokens_in=1000, tokens_out=0)
    assert fallback == Decimal(".3230")
    assert snapshot["basis"] == "token_tariff"


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"Location": "https://other.invalid"}),
    httpx.Response(503), httpx.Response(200, content=b"not-json")])
async def test_catalog_bad_response_fails_closed(monkeypatch, response):
    original = httpx.AsyncClient
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs:
        original(transport=httpx.MockTransport(lambda r: response), **kwargs))
    assert await pricing._fetch_catalog_prices() == {}


async def test_catalog_timeout_does_not_invent_price(monkeypatch):
    original = httpx.AsyncClient
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs:
        original(transport=httpx.MockTransport(timeout), **kwargs))
    assert await pricing._fetch_catalog_prices() == {}
