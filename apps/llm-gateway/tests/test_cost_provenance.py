"""Cost audit selection records evidence without changing price or inventing FX."""

from decimal import Decimal

import pytest

from yleum_gateway.services import pricing

MODEL = "gemini-3.1-pro-preview-customtools"


def test_reported_header_branch_is_recorded_with_exact_currency_and_no_body():
    data = {"usage": {"cost_rub": "8", "cost_usd": "0.02"}, "content": "PRIVATE BODY"}
    read = pricing.read_reported_cost(data, {"x-llmgw-cost-rub": "7.1250", "authorization": "PRIVATE"})
    evidence = pricing.cost_provenance(
        MODEL, Decimal("9"), read, tokens_in=10, tokens_out=20,
    )
    assert read.cost_rub == Decimal("7.1250")
    assert evidence["basis"] == "provider_reported_rub"
    assert evidence["rub_source"] == "header:x-llmgw-cost-rub"
    assert evidence["effective_cost_rub"] == "7.1250"
    assert evidence["provider_cost_usd"] == "0.02"
    assert evidence["usd_source"] == "usage:cost_usd"
    assert "PRIVATE" not in str(evidence)
    assert "content" not in evidence and "authorization" not in evidence


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "bad", True])
def test_invalid_header_falls_through_to_valid_zero_rub(bad):
    read = pricing.read_reported_cost({"usage": {"cost_rub": "0"}}, {"x-llmgw-cost-rub": bad})
    assert read.cost_rub == Decimal("0")
    assert read.rub_source == "usage:cost_rub"
    evidence = pricing.cost_provenance(MODEL, Decimal("8"), read, tokens_in=1, tokens_out=2)
    assert evidence["basis"] == "provider_reported_rub"
    assert evidence["effective_cost_rub"] == "0"


def test_usd_only_does_not_convert_or_replace_current_token_tariff():
    read = pricing.read_reported_cost({"usage": {"cost_usd": "0.7"}}, {})
    calculated = pricing.calculate_cost_rub(MODEL, 1000, 1000, 200, 100)
    evidence = pricing.cost_provenance(
        MODEL, calculated, read, tokens_in=1000, tokens_out=1000,
        cache_read_tokens=200, cache_write_tokens=100,
    )
    assert evidence["basis"] == "token_tariff"
    assert evidence["effective_cost_rub"] == str(calculated)
    assert evidence["provider_cost_usd"] == "0.7"
    assert evidence["reported_cost_rub"] is None
    assert "fx_rate" not in evidence
    assert evidence["tariff"]["cache_read_factor"] == "0.1"
    assert evidence["tariff"]["cache_write_factor"] == "1.25"


def test_tariff_fingerprint_changes_when_table_or_cache_factor_changes(monkeypatch):
    first = pricing.cost_provenance(MODEL, Decimal("1"), tokens_in=1, tokens_out=1)
    table = dict(pricing.PRICE_TABLE)
    table[MODEL] = pricing.ModelPrice(Decimal("1.51"), Decimal("7.50"))
    monkeypatch.setattr(pricing, "PRICE_TABLE", table)
    second = pricing.cost_provenance(MODEL, Decimal("1"), tokens_in=1, tokens_out=1)
    assert first["tariff_revision"] != second["tariff_revision"]
    monkeypatch.setattr(pricing, "_CACHE_HIT_RATE", Decimal("0.2"))
    third = pricing.cost_provenance(MODEL, Decimal("1"), tokens_in=1, tokens_out=1)
    assert second["tariff_revision"] != third["tariff_revision"]


def test_stream_estimate_is_labeled_as_estimate_without_provider_charge():
    evidence = pricing.cost_provenance(
        MODEL, Decimal("1.2345"), tokens_in=10, tokens_out=20, estimated_tokens=True,
    )
    assert evidence["basis"] == "local_token_estimate"
    assert evidence["reported_cost_rub"] is None
    assert evidence["provider_cost_usd"] is None


def test_untrusted_provenance_keys_or_invalid_amounts_are_rejected():
    evidence = pricing.cost_provenance(MODEL, Decimal("1"), tokens_in=1, tokens_out=1)
    with pytest.raises(ValueError):
        pricing.validate_cost_provenance({**evidence, "prompt": "PRIVATE"})
    with pytest.raises(ValueError):
        pricing.validate_cost_provenance({**evidence, "effective_cost_rub": "NaN"})
    with pytest.raises(ValueError):
        pricing.validate_cost_provenance(evidence, cost_rub=Decimal("2"))
    with pytest.raises(ValueError):
        pricing.validate_cost_provenance({**evidence, "schema_version": True})
    assert pricing.validate_cost_provenance(evidence, cost_rub=Decimal("1")) == evidence
