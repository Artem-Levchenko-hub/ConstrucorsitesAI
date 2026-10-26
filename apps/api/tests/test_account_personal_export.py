"""Real HTTP/SQL confidentiality checks for the personal-data download."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from yleum_api.core.config import get_settings
from yleum_api.core.security import create_access_token
from yleum_api.models.account import AuthSession, AuthToken, LegalAcceptance, Payment
from yleum_api.models.billing import BillingAccount
from yleum_api.models.oauth_login import UserIdentity
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.models.wallet_charge import WalletCharge


async def test_export_uses_current_owner_and_never_serializes_auth_secrets(client, db_session):
    now = datetime.now(UTC)
    owners = [User(
        id=uuid4(), email=f"export-{uuid4().hex}@example.com",
        password_hash=f"private-password-hash-{i}", github_token_enc=f"private-github-token-{i}",
        phone_e164=f"+7999123456{i}", phone_verified_at=now,
    ) for i in range(2)]
    db_session.add_all(owners)
    await db_session.flush()
    for i, owner in enumerate(owners):
        account = BillingAccount(personal_user_id=owner.id, created_by_user_id=owner.id,
                                 scope="personal")
        db_session.add(account)
        await db_session.flush()
        db_session.add_all([
            UserIdentity(user_id=owner.id, provider="yandex", provider_user_id=f"own-login-{i}",
                         email=owner.email),
            Project(owner_id=owner.id, name=f"Personal project {i}",
                    slug=f"export-{uuid4().hex}", template="max_miniapp"),
            LegalAcceptance(user_id=owner.id, document_type="privacy",
                            document_version="fixture", ip_address=f"192.0.2.{i + 1}"),
            AuthSession(user_id=owner.id, user_agent=f"Own device {i}",
                        expires_at=now + timedelta(days=1)),
            AuthToken(user_id=owner.id, purpose="reset_password",
                      token_hash=f"private-reset-token-{i}", expires_at=now + timedelta(hours=1)),
            Wallet(user_id=owner.id, billing_account_id=account.id,
                   balance_rub=Decimal(10 + i)),
            Payment(user_id=owner.id, billing_account_id=account.id,
                    idempotency_key=f"private-payment-key-{i}", package_code="start",
                    amount_rub=Decimal(100 + i), credit_rub=Decimal(100 + i),
                    provider_payload={"secret": f"private-provider-receipt-{i}"}),
            WalletCharge(user_id=owner.id, billing_account_id=account.id,
                         entry_type="adjustment", amount_rub=Decimal(i + 1),
                         balance_after_rub=Decimal(10 + i), description=f"private-reason-{i}"),
        ])
    await db_session.commit()
    client.cookies.set(get_settings().jwt_cookie_name, create_access_token(owners[0].id))
    response = await client.get(f"/api/account/export?user_id={owners[1].id}&include_files=true")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].endswith('"yleum-account-data.json"')
    data = response.json()
    assert data["account"]["id"] == str(owners[0].id)
    assert data["account"]["email"] == owners[0].email
    assert data["account"]["phone_e164"] == owners[0].phone_e164
    assert data["account"]["phone_verified_at"] is not None
    assert owners[1].phone_e164 not in response.text
    assert "code_hash" not in response.text and "phone_challenges" not in data
    assert [x["provider_user_id"] for x in data["identities"]] == ["own-login-0"]
    assert "own-login-1" not in response.text
    assert [x["name"] for x in data["projects"]] == ["Personal project 0"]
    assert [x["user_agent"] for x in data["sessions"]] == ["Own device 0"]
    assert [x["ip_address"] for x in data["consents"]] == ["192.0.2.1"]
    assert [x["balance_rub"] for x in data["wallet"]] == ["10.0000"]
    assert [x["amount_rub"] for x in data["payments"]] == ["100.00"]
    assert [x["amount_rub"] for x in data["transactions"]] == ["1.0000"]
    assert str(owners[1].id) not in response.text and owners[1].email not in response.text
    assert "private-" not in response.text
    assert "password_hash" not in response.text and "token_hash" not in response.text
    assert "github_token_enc" not in response.text
    assert "scope" in data and "excluded" in data
    # Export is read-only: authentication records remain untouched.
    tokens = list((await db_session.scalars(select(AuthToken))).all())
    assert len(tokens) == 2 and all(t.used_at is None for t in tokens)


async def test_personal_export_requires_authenticated_session(client):
    response = await client.get("/api/account/export?user_id=" + str(uuid4()))
    assert response.status_code == 401
