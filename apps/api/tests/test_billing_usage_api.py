"""Account usage journal (GET /api/billing/usage) and plan entitlements over HTTP.

Every refusal is asserted on the real endpoint with a plan row whose limits
are known, and every figure of the report is checked against rows written to
the tables it aggregates — nothing is read back from a mock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_cell_publication_api import seed as seed_publication
from yleum_api.core.config import get_settings
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.billing_usage_event import BillingUsageEvent
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.usage import Usage
from yleum_api.models.user import User
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.services import integration_providers, orchestrator_client, project_cell_runtime
from yleum_api.services import repo as repo_svc

pytestmark = pytest.mark.asyncio


async def _register(client: httpx.AsyncClient, session: AsyncSession, email: str) -> User:
    response = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert response.status_code == 201, response.text
    cookie_name = get_settings().jwt_cookie_name
    token = response.cookies.get(cookie_name)
    assert token
    client.cookies.clear()
    client.cookies.set(cookie_name, token)
    return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _limited_plan(
    session: AsyncSession, user_id: object, **entitlements: object
) -> BillingPlan:
    """Move the user's live subscription onto an inactive plan with known terms."""
    plan = BillingPlan(
        id=uuid4(),
        code=f"test_limited_{uuid4().hex[:6]}",
        version=1,
        name="Limited",
        price_rub=Decimal("0.00"),
        billing_interval="month",
        included_credit_rub=Decimal("0"),
        entitlements=entitlements,
        sort_order=99,
        is_active=False,
    )
    session.add(plan)
    await session.flush()
    account = (
        await session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == user_id)
        )
    ).scalar_one()
    subscription = (
        await session.execute(
            select(Subscription).where(
                Subscription.billing_account_id == account.id,
                Subscription.status == "active",
            )
        )
    ).scalar_one()
    subscription.plan_id = plan.id
    await session.commit()
    return plan


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)


@pytest_asyncio.fixture
async def advisory_mode(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setenv("ENFORCE_PLAN_ENTITLEMENTS", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


async def _create_project(client: httpx.AsyncClient, name: str) -> httpx.Response:
    return await client.post("/api/projects", json={"name": name, "template": "max_miniapp"})


def _entitlement(report: dict[str, object], key: str) -> dict[str, object]:
    items = report["entitlements"]
    assert isinstance(items, list)
    return next(item for item in items if item["key"] == key)


async def test_usage_report_aggregates_every_ledger_of_the_account(
    client: httpx.AsyncClient, db_session: AsyncSession, git: None
) -> None:
    user = await _register(client, db_session, "usage@example.com")
    first = await _create_project(client, "Первое приложение")
    second = await _create_project(client, "Второе приложение")
    assert (first.status_code, second.status_code) == (201, 201), second.text
    project_id = first.json()["id"]
    account = (
        await db_session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == user.id)
        )
    ).scalar_one()

    run = GenerationRun(
        project_id=project_id,
        user_id=user.id,
        idempotency_key="usage-run",
        prompt_hash="0" * 64,
        status="completed",
    )
    failed = GenerationRun(
        project_id=project_id,
        user_id=user.id,
        idempotency_key="usage-run-failed",
        prompt_hash="1" * 64,
        status="failed",
    )
    db_session.add_all([run, failed])
    await db_session.flush()
    db_session.add_all(
        [
            Usage(
                user_id=user.id,
                project_id=project_id,
                run_id=run.id,
                model_id="claude-sonnet-5",
                tokens_in=1000,
                tokens_out=200,
                cost_rub=Decimal("3.5000"),
                stage="native_agent",
            ),
            Usage(
                user_id=user.id,
                project_id=project_id,
                run_id=run.id,
                model_id="claude-sonnet-5",
                tokens_in=500,
                tokens_out=100,
                cost_rub=Decimal("1.2500"),
                stage="verification",
            ),
            Usage(
                user_id=user.id,
                project_id=project_id,
                run_id=None,
                model_id="gemini-3.1-pro-preview-customtools",
                tokens_in=300,
                tokens_out=50,
                cost_rub=Decimal("0.4000"),
                stage="runtime_ai",
            ),
            Usage(
                user_id=user.id,
                project_id=None,
                run_id=None,
                model_id="claude-haiku",
                tokens_in=100,
                tokens_out=20,
                cost_rub=Decimal("0.0500"),
                stage=None,
            ),
            WalletCharge(
                billing_account_id=account.id,
                user_id=user.id,
                entry_type="usage",
                amount_rub=Decimal("-5.1500"),
                balance_after_rub=Decimal("94.8500"),
                external_ref="usage:test-1",
                description="AI",
            ),
            WalletCharge(
                billing_account_id=account.id,
                user_id=user.id,
                entry_type="topup",
                amount_rub=Decimal("50.0000"),
                balance_after_rub=Decimal("144.8500"),
                external_ref="topup:test-1",
                description="Top-up",
            ),
            BillingUsageEvent(
                billing_account_id=account.id,
                user_id=user.id,
                project_id=project_id,
                kind="publication",
                external_ref="publication:test-1",
                details={"backend": "project_cell"},
            ),
            BillingUsageEvent(
                billing_account_id=account.id,
                user_id=user.id,
                project_id=project_id,
                kind="publication",
                external_ref="publication:test-2",
                details={"backend": "project_cell"},
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/billing/usage")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["period"]["source"] == "calendar_month"
    assert report["plan"]["code"] == "free" and report["plan"]["version"] == 2
    assert report["subscription_status"] == "active"
    assert report["generations"] == {
        "calls": 2,
        "cost_rub": "4.7500",
        "tokens_in": 1500,
        "tokens_out": 300,
        "total": 2,
        "completed": 1,
        "failed": 1,
        "cancelled": 0,
        "active": 0,
    }
    assert report["app_ai_answers"] == {
        "calls": 1,
        "cost_rub": "0.4000",
        "tokens_in": 300,
        "tokens_out": 50,
    }
    assert report["other_ai"]["calls"] == 1 and report["other_ai"]["cost_rub"] == "0.0500"
    assert Decimal(report["total_ai_cost_rub"]) == Decimal("5.2000")
    assert report["publications"] == {"total": 2, "projects": 1}
    assert report["wallet"] == {
        "balance_rub": "100.0000",
        "debited_rub": "5.1500",
        "credited_rub": "50.0000",
        "charges": 2,
    }
    assert report["free_generations"] == {"limit": 3, "used": 0, "left": 3, "unlimited": False}
    projects = _entitlement(report, "max_projects")
    assert (projects["limit"], projects["used"], projects["exceeded"]) == (None, 2, False)
    published = _entitlement(report, "static_publish_slots")
    assert (published["limit"], published["used"]) == (None, 1)
    integrations = _entitlement(report, "integrations")
    assert (integrations["kind"], integrations["enabled"], integrations["used"]) == (
        "flag",
        True,
        0,
    )
    assert [item["key"] for item in report["entitlements"]] == [
        "max_projects",
        "static_publish_slots",
        "always_on_slots",
        "team_seats",
        "integrations",
    ]

    # An explicit window excludes rows written outside of it.
    empty = await client.get(
        "/api/billing/usage",
        params={"from": "2020-01-01T00:00:00Z", "to": "2020-02-01T00:00:00Z"},
    )
    assert empty.status_code == 200
    assert empty.json()["period"]["source"] == "custom"
    assert empty.json()["generations"]["total"] == 0
    assert empty.json()["publications"] == {"total": 0, "projects": 0}
    # ...but the plan and its live use do not depend on the window.
    assert _entitlement(empty.json(), "max_projects")["used"] == 2

    invalid = await client.get(
        "/api/billing/usage",
        params={"from": "2026-09-02T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_failed"


async def test_usage_report_requires_a_signed_in_user(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/billing/usage")
    assert response.status_code == 401


async def test_project_limit_refuses_one_more_app_with_a_precise_reason(
    client: httpx.AsyncClient, db_session: AsyncSession, git: None
) -> None:
    user = await _register(client, db_session, "limited@example.com")
    plan = await _limited_plan(db_session, user.id, max_projects=1)

    assert (await _create_project(client, "Первое")).status_code == 201
    refused = await _create_project(client, "Второе")
    assert refused.status_code == 402, refused.text
    error = refused.json()["error"]
    assert error["code"] == "entitlement_exceeded"
    assert error["details"] == {
        "entitlement": "max_projects",
        "limit": 1,
        "used": 1,
        "plan_code": plan.code,
        "plan_version": 1,
    }
    assert "не больше 1" in error["message"]

    report = (await client.get("/api/billing/usage")).json()
    projects = _entitlement(report, "max_projects")
    assert (projects["limit"], projects["used"], projects["exceeded"]) == (1, 1, False)


async def test_project_limit_is_advisory_when_enforcement_is_off(
    client: httpx.AsyncClient, db_session: AsyncSession, git: None, advisory_mode: None
) -> None:
    user = await _register(client, db_session, "advisory@example.com")
    await _limited_plan(db_session, user.id, max_projects=1)
    assert (await _create_project(client, "Первое")).status_code == 201
    assert (await _create_project(client, "Второе")).status_code == 201
    report = (await client.get("/api/billing/usage")).json()
    assert _entitlement(report, "max_projects")["exceeded"] is True


async def test_integrations_flag_gates_connecting_a_service(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    git: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _register(client, db_session, "no-integrations@example.com")
    await _limited_plan(db_session, user.id, integrations=False)
    project_id = (await _create_project(client, "Магазин")).json()["id"]
    verify = AsyncMock(return_value="Магазин 123456")
    monkeypatch.setattr(integration_providers, "verify_provider", verify)

    refused = await client.put(
        f"/api/projects/{project_id}/app-integrations/yookassa",
        json={"values": {"shop_id": "123456", "secret_key": "live_secret_value"}},
    )
    assert refused.status_code == 402, refused.text
    error = refused.json()["error"]
    assert error["code"] == "subscription_entitlement_required"
    assert error["details"]["entitlement"] == "integrations"
    verify.assert_not_awaited()

    bind = await client.post(f"/api/projects/{project_id}/app-integrations/yookassa/bind")
    assert bind.status_code == 402
    pack = await client.post(f"/api/projects/{project_id}/app-integrations/pack/apply")
    assert pack.status_code == 402
    oauth = await client.post(
        f"/api/projects/{project_id}/app-integrations/yandex_metrica/oauth/start"
    )
    assert oauth.status_code == 402
    # Reading the catalogue stays open: the plan hides nothing, it only refuses
    # to connect.
    assert (await client.get(f"/api/projects/{project_id}/app-integrations")).status_code == 200


async def test_free_plan_connects_integrations(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    git: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _register(client, db_session, "free-integrations@example.com")
    project_id = (await _create_project(client, "Магазин")).json()["id"]
    monkeypatch.setattr(
        integration_providers, "verify_provider", AsyncMock(return_value="Магазин 1")
    )
    connected = await client.put(
        f"/api/projects/{project_id}/app-integrations/yookassa",
        json={"values": {"shop_id": "1", "secret_key": "live_secret_value"}},
    )
    assert connected.status_code == 200, connected.text
    report = (await client.get("/api/billing/usage")).json()
    assert _entitlement(report, "integrations")["used"] == 1


async def _billing_for_seeded_owner(
    session: AsyncSession, user: User, **entitlements: object
) -> BillingAccount:
    account = BillingAccount(scope="personal", personal_user_id=user.id, created_by_user_id=user.id)
    session.add(account)
    await session.flush()
    plan = BillingPlan(
        id=uuid4(),
        code=f"test_publish_{uuid4().hex[:6]}",
        version=1,
        name="Publish test",
        price_rub=Decimal("0.00"),
        billing_interval="month",
        included_credit_rub=Decimal("0"),
        entitlements=entitlements,
        sort_order=99,
        is_active=False,
    )
    session.add(plan)
    await session.flush()
    session.add(
        Subscription(
            billing_account_id=account.id,
            user_id=user.id,
            plan_id=plan.id,
            status="active",
        )
    )
    await session.commit()
    return account


def _ready_cell(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    submit = AsyncMock(return_value={"phase": "queued", "run_id": "public-one"})
    monkeypatch.setattr(orchestrator_client, "publish_project_cell", submit)
    monkeypatch.setattr(
        project_cell_runtime,
        "_get_cell_resources",
        AsyncMock(return_value=SimpleNamespace(state="resources_ready")),
    )
    return submit


async def test_publication_is_journaled_and_holds_one_slot_per_project(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, _ = await seed_publication(db_session)
    project = value["project"]
    user = await db_session.get(User, project.owner_id)
    assert user is not None
    account = await _billing_for_seeded_owner(db_session, user, static_publish_slots=1)
    submit = _ready_cell(monkeypatch)

    first = await client.post(
        f"/api/projects/{project.id}/deploy", json={"idempotency_key": "publish-once"}
    )
    assert first.status_code == 200, first.text
    events = list(
        (
            await db_session.execute(
                select(BillingUsageEvent).where(BillingUsageEvent.billing_account_id == account.id)
            )
        ).scalars()
    )
    assert [(e.kind, e.project_id, e.external_ref) for e in events] == [
        ("publication", project.id, f"publication:{project.id}:publish-once")
    ]
    assert events[0].details["backend"] == "project_cell"
    assert events[0].details["commit_sha"] == "a" * 40

    # The same request again (retry) is not a second publication in the journal;
    # a new key is, but the app still holds a single slot.
    retry = await client.post(
        f"/api/projects/{project.id}/deploy", json={"idempotency_key": "publish-once"}
    )
    assert retry.status_code == 200, retry.text
    again = await client.post(
        f"/api/projects/{project.id}/deploy", json={"idempotency_key": "publish-twice"}
    )
    assert again.status_code == 200, again.text
    assert submit.await_count == 3

    report = (await client.get("/api/billing/usage")).json()
    assert report["publications"] == {"total": 2, "projects": 1}
    slots = _entitlement(report, "static_publish_slots")
    assert (slots["limit"], slots["used"], slots["exceeded"]) == (1, 1, False)


async def test_publication_is_refused_before_the_controller_when_the_plan_has_no_slots(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, _ = await seed_publication(db_session)
    project = value["project"]
    user = await db_session.get(User, project.owner_id)
    assert user is not None
    account = await _billing_for_seeded_owner(db_session, user, static_publish_slots=0)
    submit = _ready_cell(monkeypatch)

    refused = await client.post(
        f"/api/projects/{project.id}/deploy", json={"idempotency_key": "publish-once"}
    )
    assert refused.status_code == 402, refused.text
    error = refused.json()["error"]
    assert error["code"] == "entitlement_exceeded"
    details = error["details"]
    assert (details["entitlement"], details["limit"], details["used"]) == (
        "static_publish_slots",
        0,
        0,
    )
    assert details["plan_code"].startswith("test_publish_") and details["plan_version"] == 1
    assert "не включает публикацию" in error["message"]
    submit.assert_not_awaited()
    assert (
        await db_session.scalar(
            select(BillingUsageEvent.id).where(BillingUsageEvent.billing_account_id == account.id)
        )
    ) is None


async def test_publication_without_a_billing_account_is_not_blocked_or_journaled(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, _ = await seed_publication(db_session)
    project = value["project"]
    submit = _ready_cell(monkeypatch)
    response = await client.post(
        f"/api/projects/{project.id}/deploy", json={"idempotency_key": "publish-once"}
    )
    assert response.status_code == 200, response.text
    submit.assert_awaited_once()
    assert (await db_session.scalar(select(BillingUsageEvent.id))) is None


async def test_deleting_a_project_frees_its_publish_slot_but_keeps_the_history(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    git: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deleting a project asks the orchestrator to destroy its cell and drops the
    # MinIO repo; neither exists here (same stubs as the project deletion tests).
    async def _destroy(project_id, slug):
        return {"state": "destroyed"}

    monkeypatch.setattr("yleum_api.services.orchestrator_client.destroy", _destroy)
    monkeypatch.setattr("yleum_api.services.repo.delete_repo", lambda project_id: None)
    user = await _register(client, db_session, "slot-release@example.com")
    project_id = (await _create_project(client, "Опубликованное")).json()["id"]
    account = (
        await db_session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == user.id)
        )
    ).scalar_one()
    db_session.add(
        BillingUsageEvent(
            billing_account_id=account.id,
            user_id=user.id,
            project_id=project_id,
            kind="publication",
            external_ref="publication:release-test",
            created_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    before = (await client.get("/api/billing/usage")).json()
    assert _entitlement(before, "static_publish_slots")["used"] == 1

    deleted = await client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 204, deleted.text
    after = (await client.get("/api/billing/usage")).json()
    assert _entitlement(after, "static_publish_slots")["used"] == 0
    assert after["publications"]["total"] == 1
