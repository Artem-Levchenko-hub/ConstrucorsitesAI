"""Positive verification receipts distinguish proved email from historical signup hints."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from yleum_api.models.account import AuthToken
from yleum_api.models.oauth_login import UserIdentity
from yleum_api.models.user import User
from yleum_api.services.accounts import has_verified_email


async def test_only_canonical_mail_receipt_or_persisted_authority_proves_email(db_session):
    now = datetime.now(UTC)
    owner = User(email="proof@example.com", password_hash="x", email_verified_at=now)
    db_session.add(owner)
    await db_session.flush()
    assert await has_verified_email(db_session, owner) is False

    receipt = AuthToken(
        user_id=owner.id,
        purpose="verify_email",
        token_hash=uuid4().hex,
        expires_at=now + timedelta(hours=1),
        used_at=now + timedelta(seconds=1),
    )
    db_session.add(receipt)
    await db_session.flush()
    # Issuance/resend also marks old tokens used: that is NOT proof of ownership.
    assert await has_verified_email(db_session, owner) is False
    receipt.used_at = now
    await db_session.flush()
    assert await has_verified_email(db_session, owner) is True
    receipt.purpose = "reset_password"
    await db_session.flush()
    assert await has_verified_email(db_session, owner) is False
    receipt.purpose = "verify_email"
    receipt.expires_at = now - timedelta(seconds=1)
    await db_session.flush()
    assert await has_verified_email(db_session, owner) is False

    owner.role = "admin"
    assert await has_verified_email(db_session, owner) is True
    owner.role = "user"
    owner.email_verified_at = None
    assert await has_verified_email(db_session, owner) is False


async def test_only_matching_known_server_oauth_identity_proves_email(db_session):
    owner = User(
        email="oauth@example.com",
        password_hash="x",
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(owner)
    await db_session.flush()
    identity = UserIdentity(
        user_id=owner.id,
        provider="untrusted",
        provider_user_id="123",
        email=owner.email,
    )
    db_session.add(identity)
    await db_session.flush()
    assert await has_verified_email(db_session, owner) is False
    for provider in ("vk", "yandex"):
        identity.provider = provider
        identity.email = "other@example.com"
        await db_session.flush()
        assert await has_verified_email(db_session, owner) is False
        identity.email = "OAUTH@EXAMPLE.COM"
        identity.provider_user_id = " "
        await db_session.flush()
        assert await has_verified_email(db_session, owner) is False
        identity.provider_user_id = "123"
        await db_session.flush()
        assert await has_verified_email(db_session, owner) is True
