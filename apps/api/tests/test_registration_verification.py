"""Staged phone rollout preserves canonical proof while the sender is pending."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User


class ProofSession:
    def __init__(self, receipt=None):
        self.receipt = receipt

    async def scalar(self, statement):
        # Canonical verification authority asks for a matching receipt or OAuth
        # identity; this isolated session returns no proof unless explicitly set.
        return self.receipt


@pytest.mark.parametrize("email_timestamp", [None, datetime.now(UTC)])
async def test_disabled_phone_rollout_rejects_unproved_and_legacy_email(
    monkeypatch, email_timestamp
):
    from yleum_api.services.registration_verification import (
        registration_verification_reason,
        require_registration_verification,
    )

    monkeypatch.setattr(get_settings(), "phone_verification_enabled", False)
    user = User(
        id=uuid4(),
        email="qa@example.com",
        status="active",
        role="user",
        email_verified_at=email_timestamp,
    )
    assert (
        await registration_verification_reason(ProofSession(), user)
        == "email_verification_required"
    )
    with pytest.raises(ApiError) as caught:
        await require_registration_verification(ProofSession(), user)
    assert caught.value.code == "email_verification_required"


async def test_disabled_phone_rollout_accepts_canonical_email_receipt(monkeypatch):
    from yleum_api.services.registration_verification import registration_verification_reason

    monkeypatch.setattr(get_settings(), "phone_verification_enabled", False)
    user = User(
        id=uuid4(),
        email="qa@example.com",
        status="active",
        role="user",
        email_verified_at=datetime.now(UTC),
    )
    assert await registration_verification_reason(ProofSession(uuid4()), user) is None


@pytest.mark.parametrize("enabled", [False, True])
async def test_phone_proof_accepts_unverified_email_in_either_rollout_stage(monkeypatch, enabled):
    from yleum_api.services.registration_verification import registration_verification_reason

    monkeypatch.setattr(get_settings(), "phone_verification_enabled", enabled)
    user = User(
        id=uuid4(),
        email="qa@example.com",
        status="active",
        role="user",
        phone_e164="+79991234567",
        phone_verified_at=datetime.now(UTC),
    )
    assert await registration_verification_reason(ProofSession(), user) is None
    assert user.email_verified_at is None


async def test_enabled_phone_rollout_requires_phone_even_with_canonical_email_and_missing_sms(
    monkeypatch,
):
    from yleum_api.services.registration_verification import registration_verification_reason

    monkeypatch.setattr(get_settings(), "phone_verification_enabled", True)
    monkeypatch.setattr(get_settings(), "smsaero_email", None)
    user = User(
        id=uuid4(),
        email="qa@example.com",
        status="active",
        role="user",
        email_verified_at=datetime.now(UTC),
    )
    assert (
        await registration_verification_reason(ProofSession(uuid4()), user)
        == "phone_verification_required"
    )
