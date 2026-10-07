from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from yleum_api.services import entitlements


def qa_context():
    owner = SimpleNamespace(id=uuid4(), role="admin")
    account = SimpleNamespace(id=uuid4(), personal_user_id=owner.id)
    plan = SimpleNamespace(
        id=uuid4(), code="qa_internal", is_active=False,
        entitlements={"qa_unlimited_generations": True},
    )
    subscription = SimpleNamespace(
        status="active", user_id=owner.id, billing_account_id=account.id,
        plan_id=plan.id, current_period_end=None,
    )
    return owner, entitlements.PlanContext(account, subscription, plan)


def test_private_qa_generation_entitlement_is_account_local_and_revocable():
    owner, context = qa_context()
    assert entitlements.has_qa_unlimited_generations(owner, context)
    owner.role = "user"
    assert not entitlements.has_qa_unlimited_generations(owner, context)
    owner.role = "admin"
    assert entitlements.has_qa_unlimited_generations(owner, context)
    assert not entitlements.has_qa_unlimited_generations(
        SimpleNamespace(id=uuid4(), role="admin"), context,
    )


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("owner", "role", "user"),
        ("plan", "code", "pro"),
        ("plan", "is_active", True),
        ("plan", "entitlements", {}),
        ("plan", "entitlements", {"qa_unlimited_generations": False}),
        ("plan", "entitlements", {"qa_unlimited_generations": "true"}),
        ("plan", "entitlements", {"qa_unlimited_generations": 1}),
        ("plan", "entitlements", None),
        ("subscription", "status", "expired"),
        ("subscription", "status", "paused"),
        ("subscription", "status", "past_due"),
        ("subscription", "plan_id", uuid4()),
        ("subscription", "user_id", uuid4()),
        ("subscription", "billing_account_id", uuid4()),
        ("subscription", "current_period_end", datetime(2020, 1, 1, tzinfo=UTC)),
        ("subscription", "current_period_end", datetime(2100, 1, 1)),
    ],
)
def test_qa_entitlement_requires_every_trusted_binding(target, field, value):
    owner, context = qa_context()
    item = owner if target == "owner" else getattr(context, target)
    setattr(item, field, value)
    assert not entitlements.has_qa_unlimited_generations(owner, context)


@pytest.mark.parametrize("missing", ["account", "subscription", "plan"])
def test_qa_entitlement_requires_attached_subscription(missing):
    owner, context = qa_context()
    values = {key: getattr(context, key) for key in ("account", "subscription", "plan")}
    values[missing] = None
    assert not entitlements.has_qa_unlimited_generations(owner, entitlements.PlanContext(**values))


def test_qa_expiry_boundary_is_not_extended_by_lifecycle_delay():
    owner, context = qa_context()
    now = datetime(2026, 10, 7, tzinfo=UTC)
    context.subscription.current_period_end = now + timedelta(seconds=1)
    assert entitlements.has_qa_unlimited_generations(owner, context, now=now)
    context.subscription.current_period_end = now
    assert not entitlements.has_qa_unlimited_generations(owner, context, now=now)


@pytest.mark.parametrize("qa,global_unlimited", [(True, False), (False, False), (False, True)])
async def test_exhausted_zero_wallet_admission_uses_only_trusted_qa_or_existing_global_flag(
    monkeypatch, qa, global_unlimited,
):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.generation import acceptance

    owner, context = qa_context()
    owner.free_generations_used = 100
    if not qa:
        context.plan.entitlements = {"qa_unlimited_generations": "true"}
    monkeypatch.setattr(entitlements, "load_plan_context", AsyncMock(return_value=context))
    monkeypatch.setattr(acceptance, "get_settings", lambda: SimpleNamespace(
        unlimited_generations=global_unlimited,
    ))
    monkeypatch.setattr(
        acceptance, "resolve_billing_account", AsyncMock(return_value=context.account),
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(prompt_text="Existing app")),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
    )
    turn = acceptance.PromptAcceptance(
        project_id=uuid4(), project=SimpleNamespace(current_snapshot_id=uuid4()),
        current_user=owner, session=session,
        payload=SimpleNamespace(prompt="Add a product page", max_config_version=None),
    )
    if qa or global_unlimited:
        await turn.check_admission()
        assert turn.is_free is True
        session.execute.assert_not_awaited()
    else:
        with pytest.raises(ApiError) as refused:
            await turn.check_admission()
        assert refused.value.code == "wallet_empty"


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_free_plan_second_message_still_denied_without_private_plan(monkeypatch, role):
    from yleum_api.core.errors import ApiError

    owner, context = qa_context()
    owner.role = role
    owner.free_generations_used = 100
    owner.free_chat_messages_used = 1
    context.plan.code = "free"
    monkeypatch.setattr(entitlements, "load_plan_context", AsyncMock(return_value=context))
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[owner, True]),
        get=AsyncMock(return_value=owner), flush=AsyncMock(),
    )
    with pytest.raises(ApiError) as refused:
        await entitlements.admit_free_chat_message(session, user_id=owner.id, run_id=uuid4())
    assert refused.value.code == "entitlement_exceeded"
    assert refused.value.details["entitlement"] == "free_chat_messages"
    session.flush.assert_not_awaited()
    session.scalar = AsyncMock(return_value=True)
    assert await entitlements.free_chat_allowance(session, owner.id) == (1, 0)


async def test_two_private_qa_owners_admit_repeated_runs_and_waive_settlement(
    client, db_session, monkeypatch,
):
    from sqlalchemy import func, select

    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.billing import BillingPlan
    from yleum_api.models.generation_billing import GenerationBillingIntent, GenerationBillingPolicy
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.message import Message
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.models.usage import Usage
    from yleum_api.models.wallet import Wallet
    from yleum_api.models.wallet_charge import WalletCharge
    from yleum_api.services.generation_billing import seal_terminal_locked
    from yleum_api.services.promotion_permit import (
        MAX_FULL_BUILD_CONTRACT_VERSION,
        MAX_RELEASE_PROOF_CONTRACT_VERSION,
    )
    from yleum_api.services.subscription_lifecycle import process_subscription_cycle

    plan = BillingPlan(
        id=uuid4(), code="qa_internal", version=1, name="Internal QA",
        price_rub=Decimal(0), included_credit_rub=Decimal(0), billing_interval="month",
        is_active=False, entitlements={"qa_unlimited_generations": True},
    )
    db_session.add(plan)
    await db_session.commit()
    owners = []
    for _ in range(2):
        owner, projects, account = await owner_projects(db_session, plan_id=plan.id, count=1)
        owner.role = "admin"
        owner.free_generations_used = 100
        owner.free_chat_messages_used = 1
        db_session.add(Wallet(user_id=owner.id, billing_account_id=account.id, balance_rub=0))
        owners.append((owner, projects[0], account))
    await db_session.commit()
    try:
        for owner, project, account in owners:
            jobs = no_providers(monkeypatch, owner)
            catalog = await client.get("/api/billing/plans")
            assert catalog.status_code == 200
            assert "qa_internal" not in {item["code"] for item in catalog.json()}
            for index, status in enumerate(("completed", "failed", "completed")):
                response = await client.post(
                    f"/api/projects/{project.id}/prompt",
                    json={"prompt": "Add another QA screen", "skip_clarify": True,
                          "idempotency_key": f"qa-{owner.id}-{index}"},
                )
                assert response.status_code == 202, response.text
                run = await db_session.get(GenerationRun, UUID(response.json()["run_id"]))
                policy = await db_session.get(GenerationBillingPolicy, run.id)
                assert policy.is_free is True and jobs[-1]["is_free"] is True
                usage = Usage(
                    id=uuid4(), user_id=owner.id, project_id=project.id,
                    message_id=run.assistant_message_id, run_id=run.id, model_id="test-only",
                    tokens_in=10, tokens_out=5, cost_rub=Decimal("2.50"),
                )
                db_session.add(usage)
                if status == "completed":
                    # Supply all accepted-build bindings: waiver must come from
                    # the admitted free policy, not from missing completion proof.
                    snapshot = Snapshot(id=uuid4(), project_id=project.id, commit_sha="a" * 40)
                    db_session.add(snapshot)
                    await db_session.flush()
                    message = await db_session.get(Message, run.assistant_message_id)
                    message.snapshot_id = snapshot.id
                    run.agent_state = {
                        **(run.agent_state or {}), "snapshot_id": str(snapshot.id),
                        "commit_sha": snapshot.commit_sha,
                        "billing_accepted_candidate": {
                            "snapshot_id": str(snapshot.id), "commit_sha": snapshot.commit_sha,
                            "build_contract": MAX_FULL_BUILD_CONTRACT_VERSION,
                            "release_contract": MAX_RELEASE_PROOF_CONTRACT_VERSION,
                            "permit_digest": "b" * 64, "proof_key": "c" * 64,
                            "artifact_digest": "d" * 64,
                        },
                    }
                run.status = status
                run.finished_at = datetime.now(UTC)
                await db_session.flush()
                await seal_terminal_locked(db_session, run)
                await db_session.commit()
                intent = await db_session.get(GenerationBillingIntent, run.id)
                assert intent.outcome == "waived" and intent.amount_rub == 0
                assert intent.usage_ids == [str(usage.id)]
            assert len(jobs) == 3
            report = await client.get("/api/billing/usage")
            assert report.status_code == 200, report.text
            body = report.json()
            assert body["free_generations"]["unlimited"] is True
            assert Decimal(body["wallet"]["balance_rub"]) == 0
            assert Decimal(body["wallet"]["debited_rub"]) == 0
            assert body["generations"]["calls"] == 3
            assert Decimal(body["generations"]["cost_rub"]) == Decimal("7.50")
            profile = await client.get("/api/auth/me")
            assert profile.json()["user_chat_messages_limit"] is None
            assert profile.json()["user_chat_messages_remaining"] is None
        assert await db_session.scalar(select(func.count()).select_from(WalletCharge)) == 0
        assert await process_subscription_cycle(
            db_session, now=datetime(2100, 1, 1, tzinfo=UTC),
        ) == 0
        # Revoking admin removes only this private exemption, even with the plan attached.
        owner.role = "user"
        await db_session.commit()
        report = await client.get("/api/billing/usage")
        assert report.json()["free_generations"]["unlimited"] is False
        denied = await client.post(
            f"/api/projects/{project.id}/prompt",
            json={"prompt": "Add another QA screen", "skip_clarify": True,
                  "idempotency_key": "qa-after-role-revoked"},
        )
        assert denied.status_code == 402 and denied.json()["error"]["code"] == "wallet_empty"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
