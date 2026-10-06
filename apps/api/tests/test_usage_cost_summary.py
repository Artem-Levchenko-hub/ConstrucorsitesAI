from decimal import Decimal

import pytest

from yleum_api.schemas.billing import UsageCostBreakdownPublic
from yleum_api.services.usage_cost_summary import add_cost, classify_cost, merge_costs


def receipt(fields):
    reported = fields.get("reported_cost_rub")
    return {"schema_version": 1, "effective_cost_rub": reported if reported is not None else "0",
            "calculated_cost_rub": "0", "reported_cost_rub": None, **fields}


@pytest.mark.parametrize("provenance,expected", [
    ({"basis": "provider_reported_rub", "reported_cost_rub": "0"}, "confirmed"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": "2.50"}, "confirmed"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": 0}, "unknown"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": "-0.00e+2"}, "confirmed"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": True}, "unknown"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": "NaN"}, "unknown"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": "Infinity"}, "unknown"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": "-1"}, "unknown"),
    ({"basis": "provider_reported_rub", "provider_cost_usd": "1"}, "unknown"),
    ({"basis": "provider_reported_rub", "reported_cost_rub": {"amount": "1"}}, "unknown"),
    ({"basis": "token_tariff"}, "estimated"),
    ({"basis": "local_token_estimate"}, "estimated"),
    ({"basis": "provider_catalog_estimate", "provider_cost_usd": "1"}, "estimated"),
    ({"basis": "unsupported"}, "unknown"), (None, "unknown"), ([], "unknown"),
])
def test_classification_requires_rub_evidence(provenance, expected):
    stored = Decimal("2.5") if isinstance(provenance, dict) and provenance.get(
        "reported_cost_rub"
    ) == "2.50" else Decimal(0)
    assert classify_cost(receipt(provenance) if isinstance(provenance, dict) else provenance,
                         stored) == expected


@pytest.mark.parametrize("reported,stored,expected", [
    ("99", "1.2", "unknown"),
    ("1.23445", "1.2345", "confirmed"),
    ("1.23445", "1.2344", "unknown"),
    ("0.00005", "0.0001", "confirmed"),
    ("1.23444", "1.2344", "confirmed"),
    ("1e99", "1", "unknown"),
    ("1e-99", "0", "confirmed"),
    ("1e999999999999999999", "0", "unknown"),
    ("1e-999999999999999999", "0", "unknown"),
    ("NaN", "0", "unknown"),
    ("9" * 81, "0", "unknown"),
])
def test_confirmed_cost_must_match_stored_numeric_rounding(reported, stored, expected):
    assert classify_cost(
        receipt({"basis": "provider_reported_rub", "reported_cost_rub": reported}), Decimal(stored)
    ) == expected


@pytest.mark.parametrize("overrides", [
    {"schema_version": 999}, {"schema_version": True}, {"schema_version": 1.0},
    {"schema_version": "1"}, {"schema_version": None},
    {"effective_cost_rub": "2"}, {"effective_cost_rub": True},
    {"calculated_cost_rub": "NaN"}, {"calculated_cost_rub": None},
    {"reported_cost_rub": "1.00001"},
])
def test_unsupported_or_inconsistent_receipt_is_unknown(overrides):
    value = receipt({"basis": "provider_reported_rub", "reported_cost_rub": "1"})
    assert classify_cost({**value, **overrides}, Decimal(1)) == "unknown"


@pytest.mark.parametrize("basis", [
    "token_tariff", "local_token_estimate", "provider_catalog_estimate",
])
def test_estimates_require_supported_consistent_costs(basis):
    value = receipt({"basis": basis, "effective_cost_rub": "1.23445",
                     "calculated_cost_rub": "1.23445"})
    assert classify_cost(value, Decimal("1.2345")) == "estimated"
    assert classify_cost(value, Decimal("1.2344")) == "unknown"
    assert classify_cost({**value, "reported_cost_rub": "1.23445"}, Decimal("1.2345")) == "unknown"
    assert classify_cost({**value, "calculated_cost_rub": "2"}, Decimal("1.2345")) == "unknown"
    assert classify_cost({"basis": basis}, Decimal(0)) == "unknown"


def test_mixed_summary_uses_stored_amount_not_reported_price():
    summary = UsageCostBreakdownPublic()
    add_cost(summary, "confirmed", 1, Decimal("2.5000"))
    add_cost(summary, "confirmed", 1, Decimal("0"))
    add_cost(summary, "estimated", 3, Decimal("1.2345"))
    add_cost(summary, "unknown", 2, Decimal("9.9999"))
    assert summary.confirmed.calls == 2
    assert summary.confirmed.cost_rub == Decimal("2.5000")
    total = sum(getattr(summary, key).cost_rub for key in ("confirmed", "estimated", "unknown"))
    assert total == Decimal("13.7344")
    assert merge_costs(summary, UsageCostBreakdownPublic()) == summary
    assert '"cost_rub":"2.5000"' in summary.model_dump_json()


def test_empty_breakdowns_are_independent_and_complete():
    first, second = UsageCostBreakdownPublic(), UsageCostBreakdownPublic()
    add_cost(first, "unknown", 1, Decimal(".25"))
    assert second.model_dump(mode="json") == {
        key: {"calls": 0, "cost_rub": "0"} for key in ("confirmed", "estimated", "unknown")
    }
