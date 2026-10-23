"""A refused primary call is distinct from an uncertain paid response."""

import httpx
import pytest

from yleum_gateway.services import text_failover


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-test-key")


def refusal(status=502, *, url=text_failover.PRIMARY_URL, **kwargs):
    return httpx.Response(status, request=httpx.Request("POST", url), **kwargs)


def test_explicit_primary_refusal_is_bounded_and_provider_specific(enabled):
    candidate = text_failover.overload_candidate(refusal(json={"error": "Bad gateway"}), "claude-sonnet-5.5")
    assert candidate == {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": "llmgw_overload_v1", "upstream_http_status": 502,
    }


@pytest.mark.parametrize("kwargs", [
    {"status": 503}, {"url": "https://api.aitunnel.ru/v1/chat/completions"},
    {"json": {"error": "failed", "usage": {"cost_rub": 0}}},
    {"json": {"choices": [{"message": {"content": "partial"}}]}},
    {"headers": {"x-cost-rub": "0"}}, {"content": b"x" * 16385},
    {"json": {"error": {"usage": {"completion_tokens": 1}}}},
    {"content": b'{"error":{"usage":{"cost_rub":1}},"error":"Bad gateway"}'},
    {"content": b'data: {"choices":[{"delta":{"content":"accepted"}}]}\nBad gateway'},
])
def test_uncertain_or_other_provider_response_is_not_refusal(enabled, kwargs):
    assert text_failover.overload_candidate(refusal(**kwargs), "claude-sonnet-5.5") is None


def test_only_explicitly_enabled_same_model_can_use_backup(enabled, monkeypatch):
    response = refusal(json={"error": "Bad gateway"})
    assert text_failover.overload_candidate(response, "claude-sonnet-5") is None
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "false")
    from yleum_gateway.core.config import reset_settings_cache
    reset_settings_cache()
    assert text_failover.overload_candidate(response, "claude-sonnet-5.5") is None


def test_large_streamed_refusal_is_stopped_before_whole_body_is_read():
    produced = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(1000):
                produced.append(4096)
                yield b"x" * 4096
    response = refusal(stream=Body())
    assert text_failover.read_refusal_body(response) is None
    assert sum(produced) == 20_480


@pytest.mark.parametrize("native", [False, True])
async def test_interrupted_backup_headers_do_not_claim_llmgw_money(enabled, monkeypatch, native):
    from yleum_gateway.core.errors import UpstreamProviderError
    from yleum_gateway.providers import llmgw
    from yleum_gateway.routers import messages_native

    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadTimeout("body interrupted")
            yield b""

    def reply(request):
        return httpx.Response(200, headers={"x-cost-usd": "99", "x-llmgw-cost-rub": "77"}, stream=BrokenBody())
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    evidence = {}
    if native:
        with pytest.raises(httpx.ReadTimeout):
            messages_native._post_llmgw("https://api.aitunnel.ru/v1/chat/completions", {}, {},
                                        evidence=evidence, provider_scope="aitunnel")
        proof = evidence
    else:
        with pytest.raises(UpstreamProviderError) as caught:
            await llmgw.acompletion(model="claude-sonnet-5.5", messages=[], max_tokens=128,
                                    provider_scope="aitunnel", retry_preconnect=False, evidence=evidence)
        proof = caught.value.details
    assert proof.get("provider_cost_rub") is None
    assert proof.get("provider_cost_usd") is None
