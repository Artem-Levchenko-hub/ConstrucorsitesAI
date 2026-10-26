"""The billing worker may read verification receipts, never authentication secrets."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_subscription_email_events import NOW, setup_paid
from yleum_api.models.account import AuthToken
from yleum_api.models.billing import BillingPlan
from yleum_api.models.email_notification import EmailNotification
from yleum_api.models.oauth_login import UserIdentity
from yleum_api.services.accounts import has_verified_email
from yleum_api.services.subscription_email import _validated_content, enqueue_paid_email

ROOT = Path(__file__).resolve().parents[3]
GRANTS = ROOT / "infra/max-k3s/commerce/remote/billing-email-grants.sql"


def test_email_grants_are_scoped_to_outbox_and_nonsecret_receipt_columns():
    sql = GRANTS.read_text()
    assert 'SELECT, INSERT, UPDATE ON TABLE public.email_notifications TO :"billing_role"' in sql
    assert "SELECT (id, user_id, purpose, used_at, expires_at)" in sql
    assert "SELECT (id, user_id, provider, provider_user_id, email)" in sql
    assert "token_hash" not in sql and "code_hash" not in sql
    assert "UPDATE ON TABLE public.auth_tokens" not in sql
    assert "UPDATE ON TABLE public.user_identities" not in sql


async def test_restricted_billing_role_can_queue_and_read_proof_but_not_auth_secrets(
    db_session,
    test_engine,
):
    user, sub, payment = await setup_paid(db_session)
    user.phone_e164, user.phone_verified_at, user.email_verified_at = None, None, NOW
    receipt = AuthToken(
        user_id=user.id,
        purpose="verify_email",
        token_hash=uuid4().hex,
        used_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    identity = UserIdentity(
        user_id=user.id, provider="yandex", provider_user_id=uuid4().hex, email=user.email
    )
    db_session.add_all([receipt, identity])
    await db_session.commit()
    plan = await db_session.get(BillingPlan, sub.plan_id)
    role = "qa_billing_email_" + uuid4().hex
    # Role creation, grants and data changes roll back together: no global QA
    # role survives, including after a failed assertion or permission error.
    async with test_engine.connect() as connection, connection.begin() as transaction:
        await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT'))
        await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await connection.execute(
            text(
                "GRANT SELECT ON users, wallets, billing_accounts, billing_plans, subscriptions, "
                f'payments TO "{role}"'
            )
        )
        sql = GRANTS.read_text().replace(':"billing_role"', f'"{role}"')
        # Strip full-line comments before splitting: prose may contain semicolons.
        sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        for statement in sql.split(";"):
            # psql wraps the helper in a transaction; this fixture already owns one.
            statement = statement.strip()
            if statement and statement not in {"BEGIN", "COMMIT"}:
                await connection.execute(text(statement))
        await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
        async with AsyncSession(bind=connection, expire_on_commit=False) as worker:
            await enqueue_paid_email(worker, payment, sub, plan)
            event = await worker.scalar(select(EmailNotification))
            assert await _validated_content(worker, event, NOW) is not None
            await worker.execute(
                update(EmailNotification)
                .where(EmailNotification.id == event.id)
                .values(status="sending")
            )
            assert await worker.scalar(select(EmailNotification.status)) == "sending"

            # Exercise the canonical OAuth fallback with the same narrow grants.
            await connection.execute(text("RESET ROLE"))
            await connection.execute(
                update(AuthToken).where(AuthToken.id == receipt.id).values(used_at=None)
            )
            await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert await has_verified_email(worker, user)

            for forbidden in (
                "SELECT token_hash FROM auth_tokens",
                "SELECT code_hash FROM auth_tokens",
                "SELECT code_hash FROM phone_challenges",
                "UPDATE auth_tokens SET used_at = now()",
                "UPDATE user_identities SET email = 'forbidden@example.test'",
                "DELETE FROM auth_tokens",
            ):
                with pytest.raises(DBAPIError) as denied:
                    async with connection.begin_nested():
                        await connection.execute(text(forbidden))
                assert denied.value.orig.sqlstate == "42501"
        await transaction.rollback()
