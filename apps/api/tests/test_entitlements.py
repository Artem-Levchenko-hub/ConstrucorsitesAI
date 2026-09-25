"""Plan entitlement semantics and the usage read model — no database needed.

The DB-backed behaviour (refusals over HTTP, journal rows, aggregation) lives in
``test_billing_usage_api.py``; this file pins the pure rules those tests rely
on, so a wrong reading of a plan's JSON cannot hide behind a fixture.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from yleum_api.core.config import Settings
from yleum_api.core.errors import ApiError
from yleum_api.models.billing import (
    DEFAULT_BILLING_PLANS,
    ENTITLEMENT_FLAG_KEYS,
    ENTITLEMENT_LIMIT_KEYS,
    FREE_PLAN_ID,
    FREE_PLAN_V1_ID,
    BillingPlan,
)
from yleum_api.services import billing_usage, entitlements
from yleum_api.services.entitlements import (
    EntitlementUsage,
    entitlement_flag,
    entitlement_limit,
)


def _plan(**terms: object) -> BillingPlan:
    return cast(
        BillingPlan, SimpleNamespace(entitlements=terms, name="Test", code="test", version=1)
    )


def test_numeric_entitlement_null_or_missing_means_no_limit() -> None:
    assert entitlement_limit(_plan(max_projects=None), "max_projects") is None
    assert entitlement_limit(_plan(), "max_projects") is None
    assert entitlement_limit(None, "max_projects") is None
    assert entitlement_limit(_plan(max_projects=3), "max_projects") == 3
    # A negative or boolean value is a configuration mistake, never a limit of -1.
    assert entitlement_limit(_plan(max_projects=-2), "max_projects") == 0
    assert entitlement_limit(_plan(max_projects=True), "max_projects") is None


def test_flag_entitlement_missing_means_allowed() -> None:
    assert entitlement_flag(_plan(), "integrations") is True
    assert entitlement_flag(None, "integrations") is True
    assert entitlement_flag(_plan(integrations=False), "integrations") is False
    assert entitlement_flag(_plan(integrations=True), "integrations") is True


def test_usage_exceeded_only_when_use_passes_the_limit() -> None:
    assert EntitlementUsage(key="max_projects", kind="limit", used=3, limit=3).exceeded is False
    assert EntitlementUsage(key="max_projects", kind="limit", used=4, limit=3).exceeded is True
    assert EntitlementUsage(key="max_projects", kind="limit", used=99, limit=None).exceeded is False
    assert EntitlementUsage(key="integrations", kind="flag", used=1, enabled=False).exceeded
    assert not EntitlementUsage(key="integrations", kind="flag", used=0, enabled=False).exceeded
    assert EntitlementUsage(key="max_projects", kind="limit", used=0, limit=1).label == "Приложений"


def test_free_v2_is_the_active_free_plan_with_the_owner_model() -> None:
    free = {plan["code"]: plan for plan in DEFAULT_BILLING_PLANS if plan["is_active"]}["free"]
    assert free["id"] == FREE_PLAN_ID
    assert free["version"] == 2
    terms = cast(dict[str, object], free["entitlements"])
    # Owner (2026-09-17): Free = full access to building and publishing, only
    # generations and app traffic are quotas, a Free app sleeps instead of
    # being always on.
    assert terms["max_projects"] is None
    assert terms["static_publish_slots"] is None
    assert terms["integrations"] is True
    assert terms["always_on_slots"] == 0
    inactive_free = [
        plan for plan in DEFAULT_BILLING_PLANS if plan["code"] == "free" and not plan["is_active"]
    ]
    assert [plan["id"] for plan in inactive_free] == [FREE_PLAN_V1_ID]
    assert set(ENTITLEMENT_LIMIT_KEYS) | set(ENTITLEMENT_FLAG_KEYS) <= set(terms)


def test_enforcement_is_on_by_default() -> None:
    assert Settings.model_fields["enforce_plan_entitlements"].default is True


def test_guarded_endpoints_reference_the_entitlement_service() -> None:
    # Defence against a refactor that silently drops a guard.
    from yleum_api.routers import app_integrations, projects, runtime
    from yleum_api.services import cell_publication

    assert "assert_can_create_project" in inspect.getsource(projects.create_project)
    assert "assert_can_publish" in inspect.getsource(cell_publication.submit_publication)
    assert "record_publication" in inspect.getsource(cell_publication.submit_publication)
    assert "assert_can_publish" in inspect.getsource(runtime.trigger_deploy)
    for handler in (
        app_integrations.connect_integration,
        app_integrations.bind_existing_integration,
        app_integrations.apply_recommended_pack,
        app_integrations.start_integration_oauth,
    ):
        assert "assert_integrations_allowed" in inspect.getsource(handler)


def test_usage_period_defaults_to_subscription_period_then_calendar_month() -> None:
    now = datetime(2026, 9, 23, 12, 30, tzinfo=UTC)
    paid = billing_usage.resolve_period(
        now=now,
        period_start=None,
        period_end=None,
        subscription_period_start=datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert (paid.source, paid.start, paid.end) == (
        "subscription",
        datetime(2026, 9, 10, tzinfo=UTC),
        now,
    )
    free = billing_usage.resolve_period(
        now=now, period_start=None, period_end=None, subscription_period_start=None
    )
    assert (free.source, free.start, free.end) == (
        "calendar_month",
        datetime(2026, 9, 1, tzinfo=UTC),
        now,
    )


def test_usage_period_custom_bounds_are_validated() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    custom = billing_usage.resolve_period(
        now=now,
        period_start=datetime(2026, 8, 1),
        period_end=datetime(2026, 9, 1),
        subscription_period_start=datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert custom.source == "custom"
    assert custom.start.tzinfo is not None and custom.end == datetime(2026, 9, 1, tzinfo=UTC)
    only_end = billing_usage.resolve_period(
        now=now, period_start=None, period_end=now, subscription_period_start=None
    )
    assert only_end.start == now - timedelta(days=30)
    with pytest.raises(ApiError) as reversed_bounds:
        billing_usage.resolve_period(
            now=now,
            period_start=datetime(2026, 9, 2, tzinfo=UTC),
            period_end=datetime(2026, 9, 1, tzinfo=UTC),
            subscription_period_start=None,
        )
    assert reversed_bounds.value.code == "validation_failed"
    with pytest.raises(ApiError):
        billing_usage.resolve_period(
            now=now,
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=now,
            subscription_period_start=None,
        )


def test_gateway_ledger_rows_are_bucketed_by_run_then_stage() -> None:
    assert billing_usage.classify_usage_row("run", None) == "generation"
    assert billing_usage.classify_usage_row("run", "runtime_ai") == "generation"
    assert billing_usage.classify_usage_row(None, "runtime_ai") == "app_ai"
    assert billing_usage.classify_usage_row(None, "native_agent") == "other"
    assert billing_usage.classify_usage_row(None, None) == "other"


def test_runtime_ai_requests_are_stamped_for_the_ledger() -> None:
    from yleum_api.routers import integration_runtime

    source = inspect.getsource(integration_runtime._request_gateway_ai)
    assert f'"stage": "{billing_usage.RUNTIME_AI_STAGE}"' in source


def test_refusal_codes_distinguish_limits_from_missing_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = entitlements.PlanContext(account=None, subscription=None, plan=_plan())
    with pytest.raises(ApiError) as over_limit:
        entitlements._refuse(ctx, "max_projects", limit=1, used=1, message="full")
    assert over_limit.value.code == "entitlement_exceeded"
    assert over_limit.value.status_code == 402
    assert over_limit.value.details == {
        "entitlement": "max_projects",
        "limit": 1,
        "used": 1,
        "plan_code": "test",
        "plan_version": 1,
    }
    with pytest.raises(ApiError) as missing_feature:
        entitlements._refuse(ctx, "integrations", limit=None, used=0, message="no")
    assert missing_feature.value.code == "subscription_entitlement_required"

    monkeypatch.setenv("ENFORCE_PLAN_ENTITLEMENTS", "false")
    from yleum_api.core.config import get_settings

    get_settings.cache_clear()
    try:
        # Advisory mode: the would-be refusal is only logged.
        entitlements._refuse(ctx, "max_projects", limit=1, used=1, message="full")
    finally:
        get_settings.cache_clear()
