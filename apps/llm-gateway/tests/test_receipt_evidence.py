from decimal import Decimal

from yleum_gateway.services.receipt_evidence import receipt_evidence


def test_ai_usage_cost_is_not_promoted_to_usd_in_failure_receipt():
    receipt = receipt_evidence({"id": "synthetic-ai", "usage": {"cost": 0.3, "cost_rub": 0.3}},
                               provider_scope="aitunnel")
    assert receipt == {"provider_request_id": "synthetic-ai",
                       "provider_cost_rub": Decimal("0.3"), "provider_cost_usd": None}


def test_ai_error_details_keep_explicit_currency_and_legacy_cost_unchanged():
    receipt = receipt_evidence({"provider_cost_rub": "0.2", "provider_cost_usd": "0.01"},
                               provider_scope="aitunnel")
    assert receipt["provider_cost_rub"] == Decimal("0.2")
    assert receipt["provider_cost_usd"] == Decimal("0.01")
    assert receipt_evidence({"usage": {"cost": "0.3"}})["provider_cost_usd"] == Decimal("0.3")
