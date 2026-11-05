import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.responses import Response

from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.max_staff_checkout import MaxStaffPaymentAttempt
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.user import User
from yleum_api.routers.max_staff_checkout import _receipt
from yleum_api.services import max_staff, max_staff_checkout, max_staff_source
from yleum_api.services.max_staff_checkout import (
    InvalidProviderPayment,
    _build_return_url,
    _observe_payment,
)


def attempt() -> MaxStaffPaymentAttempt:
    return MaxStaffPaymentAttempt(
        id=uuid4(),
        project_id=uuid4(),
        order_id=uuid4(),
        owner_id=uuid4(),
        binding_id=uuid4(),
        integration_id=uuid4(),
        actor_id=42,
        shop_id="shop-1",
        auth_mode="oauth",
        grant_revision=1,
        amount_kopecks=1250,
        currency="RUB",
        request_body={},
        provider_payment_id="payment-1234",
        status="pending",
        created_at=datetime.now(UTC),
        attempts=0,
    )


def provider_payment(row: MaxStaffPaymentAttempt, **patch: object) -> dict[str, object]:
    return {
        "id": "payment-1234",
        "status": "succeeded",
        "paid": True,
        "test": False,
        "recipient": {"account_id": row.shop_id},
        "amount": {"value": "12.50", "currency": "RUB"},
        "metadata": {"yleum_staff_attempt_id": str(row.id)},
        **patch,
    }


@pytest.mark.parametrize("test", [False, True])
def test_verified_payment_keeps_live_and_test_success_distinct(test):
    row = attempt()
    observed = _observe_payment(row, provider_payment(row, test=test))
    assert observed.status == "succeeded"
    assert observed.is_test is test


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "other-payment"},
        {"recipient": {"account_id": "other"}},
        {"amount": {"value": "12.51", "currency": "RUB"}},
        {"amount": {"value": "12.50", "currency": "USD"}},
        {"metadata": {}},
        {"test": None},
        {"test": "false"},
        {"paid": False},
    ],
)
def test_provider_get_rejects_unproven_order_payment(patch):
    row = attempt()
    with pytest.raises(InvalidProviderPayment):
        _observe_payment(row, provider_payment(row, **patch))


@pytest.mark.parametrize(
    "supplied",
    [
        "https://unrelated.example/",
        "https://demo.apps.example:443/",
        "https://demo.apps.example/?next=https://unrelated.example/",
        "https://user@demo.apps.example/",
        "https://demo.apps.example/#hash",
    ],
)
def test_checkout_return_url_is_bound_to_published_root(supplied):
    with pytest.raises(ApiError) as error:
        _build_return_url("https://demo.apps.example/api/omnia/owner", uuid4(), supplied)
    assert error.value.status_code == 422


def test_checkout_return_url_carries_only_order_correlation():
    order = uuid4()
    assert _build_return_url("https://demo.apps.example/api/omnia/owner", order, None) == (
        f"https://demo.apps.example/?checkout_order_id={order}"
    )


async def test_provider_create_retry_reuses_wire_key_body_and_then_reads_known_id(monkeypatch):
    row = attempt()
    row.provider_payment_id = None
    row.request_body = {"amount": {"value": "12.50", "currency": "RUB"}, "capture": True}
    seen = []

    def response(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("Idempotence-Key"),
                request.content,
            )
        )
        assert request.headers.get("Authorization") == "Bearer synthetic-only"
        return httpx.Response(200, json={})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: client(
            transport=httpx.MockTransport(response),
            **kw,
        ),
    )
    for _ in range(2):
        await max_staff_checkout._call_provider(
            row,
            {"access_token": "synthetic-only"},
            auth_mode="oauth",
        )
    row.provider_payment_id = "payment-1234"
    await max_staff_checkout._call_provider(
        row,
        {"access_token": "synthetic-only"},
        auth_mode="oauth",
    )
    assert seen[0] == seen[1]
    assert seen[0][:3] == ("POST", "/v3/payments", str(row.id))
    assert seen[2] == ("GET", "/v3/payments/payment-1234", None, b"")


@pytest.fixture
async def checkout_context(db_session, test_engine, monkeypatch):
    owner_id, project_id, order_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    db_session.add(User(id=owner_id))
    await db_session.flush()
    db_session.add(
        Project(
            id=project_id,
            owner_id=owner_id,
            name="QA",
            slug="checkout-test",
            template="max_miniapp",
        )
    )
    connection = AccountIntegration(
        id=uuid4(),
        user_id=owner_id,
        created_by_user_id=owner_id,
        provider="yookassa",
        auth_mode="oauth",
        grant_revision=1,
        public_config={"shop_id": "shop-1"},
        credentials_enc=encrypt_strong(json.dumps({"access_token": "synthetic-only"})),
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        ProjectIntegrationBinding(
            id=uuid4(),
            project_id=project_id,
            integration_id=connection.id,
            provider="yookassa",
        )
    )
    await db_session.commit()

    async def settings(_session, selected):
        assert selected == project_id
        return SimpleNamespace(owner_id=owner_id, orders_enabled_at=now - timedelta(minutes=1))

    async def read_order(_session, selected, selected_order, actor):
        assert (selected, selected_order, actor) == (project_id, order_id, 42)
        return max_staff_source.StaffCheckoutOrder(order_id, 1250, "RUB", now, 42)

    async def target(project):
        assert project.id == project_id
        return SimpleNamespace(url="https://checkout-test.apps.example/api/omnia/owner")

    monkeypatch.setattr(max_staff, "get_order_checkout_settings", settings)
    monkeypatch.setattr(max_staff_source, "read_staff_checkout_order", read_order)
    monkeypatch.setattr(max_staff_checkout, "_target", target)
    return SimpleNamespace(
        owner=owner_id,
        project=project_id,
        order=order_id,
        connection=connection.id,
        factory=async_sessionmaker(test_engine, expire_on_commit=False),
    )


async def make_due(session, attempt_id):
    row = await session.get(MaxStaffPaymentAttempt, attempt_id, populate_existing=True)
    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()


async def test_concurrent_checkout_calls_create_one_provider_payment(
    checkout_context,
    monkeypatch,
):
    ctx = checkout_context
    calls = []

    async def provider(row, _credentials, **_):
        calls.append(row.id)
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=provider_payment(row, status="pending", paid=False))

    async def checkout():
        async with ctx.factory() as session:
            return await max_staff_checkout.create_staff_checkout(
                session,
                ctx.project,
                ctx.order,
                42,
                None,
            )

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    left, right = await asyncio.gather(checkout(), checkout())
    assert left.id == right.id
    assert calls == [left.id]


@pytest.mark.parametrize("test", [False, True])
async def test_lost_create_replays_the_durable_attempt_and_get_alone_confirms_payment(
    checkout_context,
    db_session,
    monkeypatch,
    test,
):
    ctx = checkout_context
    calls = []

    async def provider(row, credentials, **_):
        assert credentials == {"access_token": "synthetic-only"}
        async with ctx.factory() as independent:
            stored = await independent.get(MaxStaffPaymentAttempt, row.id)
            assert stored is not None and stored.request_body == row.request_body
        calls.append((row.id, row.provider_payment_id, row.request_body))
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic lost response")
        return httpx.Response(200, json=provider_payment(row, test=test))

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    row = await max_staff_checkout.create_staff_checkout(
        db_session, ctx.project, ctx.order, 42, None
    )
    assert row.status == "pending" and row.provider_payment_id is None
    assert row.request_body["amount"] == {"value": "12.50", "currency": "RUB"}
    attempt_id = row.id
    await make_due(db_session, attempt_id)
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    row = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert row.status == "pending" and row.provider_payment_id == "payment-1234"
    assert calls[0] == calls[1]
    assert (
        await max_staff_checkout.read_staff_payment_proofs(
            db_session,
            project_id=ctx.project,
            owner_id=ctx.owner,
            order_amounts={ctx.order: 1250},
            test=test,
        )
        == {}
    )
    await db_session.commit()
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    row = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert row.status == "succeeded" and row.is_test is test
    assert [call[1] for call in calls] == [None, None, "payment-1234"]
    assert await max_staff_checkout.read_staff_payment_proofs(
        db_session,
        project_id=ctx.project,
        owner_id=ctx.owner,
        order_amounts={ctx.order: 1250},
        test=test,
    ) == {ctx.order: row.paid_at}
    for owner, amount, test_mode in [
        (uuid4(), 1250, test),
        (ctx.owner, 1251, test),
        (ctx.owner, 1250, not test),
    ]:
        assert (
            await max_staff_checkout.read_staff_payment_proofs(
                db_session,
                project_id=ctx.project,
                owner_id=owner,
                order_amounts={ctx.order: amount},
                test=test_mode,
            )
            == {}
        )
    with pytest.raises(ApiError) as error:
        await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 43)
    assert error.value.status_code == 404


@pytest.mark.parametrize("changed", ["shop", "grant", "expired"])
async def test_ambiguous_create_is_never_reissued_for_another_authority_or_after_ttl(
    checkout_context,
    db_session,
    monkeypatch,
    changed,
):
    ctx = checkout_context
    calls = 0

    async def provider(_row, _credentials, **_):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("synthetic lost response")

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    row = await max_staff_checkout.create_staff_checkout(
        db_session, ctx.project, ctx.order, 42, None
    )
    if changed == "expired":
        row.created_at = datetime.now(UTC) - timedelta(hours=24)
    else:
        connection = await db_session.get(AccountIntegration, ctx.connection)
        if changed == "grant":
            connection.grant_revision += 1
        else:
            connection.public_config = {"shop_id": "another-shop"}
    await make_due(db_session, row.id)
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    assert calls == 1
    result = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert result.id == row.id and result.shop_id == "shop-1"
    assert result.status == ("needs_review" if changed == "expired" else "pending")


@pytest.mark.parametrize("rotation", ["rebind", "basic", "wrong-shop"])
async def test_known_payment_reads_same_shop_after_authorized_rotation(
    checkout_context,
    db_session,
    monkeypatch,
    rotation,
):
    ctx = checkout_context
    calls = []

    async def provider(row, credentials, *, auth_mode=None):
        calls.append((row.provider_payment_id, auth_mode or row.auth_mode, set(credentials)))
        return httpx.Response(200, json=provider_payment(row))

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    row = await max_staff_checkout.create_staff_checkout(
        db_session, ctx.project, ctx.order, 42, None
    )
    assert row.status == "pending" and row.provider_payment_id is not None
    connection = await db_session.get(AccountIntegration, ctx.connection)
    if rotation == "rebind":
        binding = await db_session.scalar(
            select(ProjectIntegrationBinding).where(
                ProjectIntegrationBinding.project_id == ctx.project,
            )
        )
        await db_session.delete(binding)
        await db_session.flush()
        db_session.add(
            ProjectIntegrationBinding(
                id=uuid4(),
                project_id=ctx.project,
                integration_id=ctx.connection,
                provider="yookassa",
            )
        )
    elif rotation == "basic":
        connection.auth_mode = "credentials"
        connection.grant_revision += 1
        connection.credentials_enc = encrypt_strong(json.dumps({"secret_key": "synthetic-only"}))
    else:
        connection.public_config = {"shop_id": "another-shop"}
    await make_due(db_session, row.id)
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    result = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert calls[0] == (None, "oauth", {"access_token"})
    if rotation == "wrong-shop":
        assert len(calls) == 1 and result.status == "pending"
    else:
        assert result.status == "succeeded"
        expected = (
            ("credentials", {"secret_key"}) if rotation == "basic" else ("oauth", {"access_token"})
        )
        assert calls[1:] == [("payment-1234", *expected)]


async def test_provider_receipt_requirement_has_a_safe_persisted_reason(
    checkout_context,
    db_session,
    monkeypatch,
):
    ctx = checkout_context

    async def provider(*_args, **_kwargs):
        return httpx.Response(
            400,
            json={
                "type": "error",
                "code": "invalid_request",
                "parameter": "receipt",
                "description": "untrusted provider error details",
            },
        )

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    row = await max_staff_checkout.create_staff_checkout(
        db_session, ctx.project, ctx.order, 42, None
    )
    receipt = _receipt(row, Response())
    assert receipt.status == "needs_review" and receipt.failure_reason == "receipt_required"
    assert receipt.confirmation_url is None and receipt.paid_at is None
    assert "untrusted" not in receipt.model_dump_json()
    await max_staff_checkout.assert_no_unsettled_staff_checkout(db_session, ctx.project)


@pytest.mark.parametrize("structured", [True, False])
async def test_first_provider_rejection_only_clears_delete_hold_with_definite_evidence(
    checkout_context,
    db_session,
    monkeypatch,
    structured,
):
    ctx = checkout_context

    async def provider(*_args, **_kwargs):
        return httpx.Response(
            400,
            json={
                "type": "error" if structured else "unknown",
                "code": "invalid_request",
                "parameter": "amount",
            },
        )

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    row = await max_staff_checkout.create_staff_checkout(
        db_session, ctx.project, ctx.order, 42, None
    )
    assert row.paid_at is None and row.confirmation_url is None
    if structured:
        assert row.status == "canceled" and row.failure_reason is None
        await max_staff_checkout.assert_no_unsettled_staff_checkout(db_session, ctx.project)
    else:
        assert row.status == "needs_review"
        with pytest.raises(ApiError, match="Обратитесь в поддержку"):
            await max_staff_checkout.assert_no_unsettled_staff_checkout(db_session, ctx.project)


@pytest.mark.parametrize("parameter", ["receipt", "amount"])
async def test_rejection_after_possible_dispatch_does_not_clear_the_delete_hold(
    checkout_context,
    db_session,
    monkeypatch,
    parameter,
):
    ctx = checkout_context
    calls = []

    async def provider(row, *_args, **_kwargs):
        async with ctx.factory() as independent:
            committed = await independent.get(MaxStaffPaymentAttempt, row.id)
            assert committed.create_dispatch_claimed is True
        calls.append(row.id)
        if len(calls) == 1:
            raise RuntimeError("interrupted after provider dispatch")
        return httpx.Response(
            400,
            json={
                "type": "error",
                "code": "invalid_request",
                "parameter": parameter,
            },
        )

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    with pytest.raises(RuntimeError, match="interrupted after provider dispatch"):
        await max_staff_checkout.create_staff_checkout(db_session, ctx.project, ctx.order, 42, None)
    row = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert row.status == "pending" and row.create_dispatch_claimed is True
    await make_due(db_session, row.id)
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    row = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert calls == [row.id, row.id]
    assert row.status == "needs_review" and row.failure_reason is None
    with pytest.raises(ApiError, match="Обратитесь в поддержку"):
        await max_staff_checkout.assert_no_unsettled_staff_checkout(db_session, ctx.project)


async def test_project_tombstone_prevents_reserving_a_new_checkout(
    checkout_context,
    db_session,
    monkeypatch,
):
    ctx = checkout_context
    db_session.add(
        ProjectCellWorkspace(
            project_id=ctx.project,
            owner_id=ctx.owner,
            provider="docker_owner_canary",
            state="deleting",
            deleted_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    async def provider(*_args, **_kwargs):
        pytest.fail("deleting project dispatched to provider")

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    with pytest.raises(ApiError) as error:
        await max_staff_checkout.create_staff_checkout(db_session, ctx.project, ctx.order, 42, None)
    assert error.value.status_code == 409
    assert await db_session.scalar(select(MaxStaffPaymentAttempt.id)) is None


async def test_account_lock_timeout_persists_backoff_and_keeps_delete_blocked(
    checkout_context,
    db_session,
    monkeypatch,
):
    ctx = checkout_context
    calls = []

    async def provider(row, *_args, **_kwargs):
        calls.append(row.id)
        raise httpx.ReadTimeout("synthetic lost response")

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    row = await max_staff_checkout.create_staff_checkout(
        db_session, ctx.project, ctx.order, 42, None
    )
    attempt_id = row.id
    await make_due(db_session, attempt_id)
    async with ctx.factory() as holder:
        await max_staff_checkout.lock_account_provider(holder, ctx.owner, "yookassa")
        await max_staff_checkout._advance(db_session, attempt_id)
    result = await max_staff_checkout.get_staff_checkout(db_session, ctx.project, ctx.order, 42)
    assert result.attempts == 2 and result.next_attempt_at > datetime.now(UTC)
    assert calls == [attempt_id]
    with pytest.raises(ApiError) as error:
        await max_staff_checkout.assert_no_unsettled_staff_checkout(db_session, ctx.project)
    assert error.value.status_code == 409


async def test_failed_rows_back_off_so_later_due_rows_are_not_starved(
    checkout_context,
    db_session,
    monkeypatch,
):
    ctx = checkout_context
    now = datetime.now(UTC)
    binding = await db_session.scalar(
        select(ProjectIntegrationBinding).where(
            ProjectIntegrationBinding.project_id == ctx.project,
        )
    )
    failed_ids = [uuid4() for _ in range(4)]
    last_id = uuid4()
    for index, attempt_id in enumerate([*failed_ids, last_id]):
        db_session.add(
            MaxStaffPaymentAttempt(
                id=attempt_id,
                project_id=ctx.project,
                order_id=uuid4(),
                owner_id=ctx.owner,
                binding_id=binding.id,
                integration_id=ctx.connection,
                actor_id=42,
                shop_id="shop-1",
                auth_mode="oauth",
                grant_revision=1,
                amount_kopecks=1250,
                currency="RUB",
                request_body={},
                status="pending",
                created_at=now,
                next_attempt_at=now - timedelta(seconds=10 - index),
            )
        )
    await db_session.commit()
    seen = []

    async def provider(row, *_args, **_kwargs):
        seen.append(row.id)
        if row.id in failed_ids:
            raise RuntimeError("synthetic unexpected transport failure")
        return httpx.Response(200, json=provider_payment(row))

    monkeypatch.setattr(max_staff_checkout, "_call_provider", provider)
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    assert seen == failed_ids
    await max_staff_checkout.reconcile_due_staff_payments(ctx.factory)
    assert seen == [*failed_ids, last_id]
