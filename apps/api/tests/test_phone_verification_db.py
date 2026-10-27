"""PostgreSQL ownership, attempt, durable budget and replay regression tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.phone import PhoneChallenge
from yleum_api.models.user import User
from yleum_api.services import phone_verification as phone
from yleum_api.services.sms_delivery import SmsDeliveryAmbiguous, SmsDeliveryFailed

NUMBER = "+79991234567"


@pytest.fixture
def outbox(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sms_provider", "greensms")
    monkeypatch.setattr(settings, "greensms_token", SecretStr("qa-key"))
    monkeypatch.setattr(settings, "phone_verification_enabled", True)
    codes = []

    async def send(number, code):
        codes.append((number, code))
        return "qa-message-id"

    monkeypatch.setattr(phone, "send_verification_sms", send)
    return codes


async def account(session, email="phone@example.com"):
    user = User(email=email, password_hash="x", is_anon=False, session_version=0)
    session.add(user)
    await session.commit()
    return user


async def test_phone_proof_is_once_and_never_marks_email_verified(db_session, outbox):
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert user.phone_e164 is None
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    assert len(claim.code_hash) == 64 and claim.code_hash != outbox[0][1]
    await phone.verify_phone_code(db_session, user, outbox[0][1])
    assert user.phone_e164 == NUMBER
    assert user.phone_verified_at is not None
    assert user.email_verified_at is None
    assert phone.phone_verification_required(user) is False
    with pytest.raises(ApiError, match="Код недействителен"):
        await phone.verify_phone_code(db_session, user, outbox[0][1])
    await db_session.rollback()


async def test_challenge_cannot_be_used_by_other_account(db_session, outbox):
    first = await account(db_session)
    other = await account(db_session, "other@example.com")
    await phone.request_phone_code(db_session, first, NUMBER, ip_address="qa")
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, other, outbox[0][1])
    assert caught.value.code == "code_invalid"
    assert other.phone_e164 is None


async def test_wrong_attempts_are_durable_and_code_exhausts_at_five(db_session, outbox):
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    wrong = "000000" if outbox[0][1] != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(ApiError) as caught:
            await phone.verify_phone_code(db_session, user, wrong)
        assert caught.value.code == "code_invalid"
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, user, outbox[0][1])
    assert caught.value.code == "code_attempts_exceeded"
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    assert claim.attempts == 5


async def test_expiry_and_password_reset_version_invalidate_otp(db_session, outbox):
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    claim.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, user, outbox[0][1])
    assert caught.value.code == "code_expired"
    claim.expires_at = datetime.now(UTC) + timedelta(minutes=10)
    user.session_version += 1
    await db_session.commit()
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, user, outbox[0][1])
    assert caught.value.code == "code_invalid"


async def test_unverified_claims_never_reserve_phone_identity(db_session, outbox):
    first = await account(db_session)
    second = await account(db_session, "second@example.com")
    await phone.request_phone_code(db_session, first, NUMBER, ip_address="qa")
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    claim.created_at -= timedelta(seconds=61)
    await db_session.commit()
    await phone.request_phone_code(db_session, second, NUMBER, ip_address="other-qa")
    await phone.verify_phone_code(db_session, second, outbox[-1][1])
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, first, outbox[0][1])
    assert caught.value.code == "phone_already_registered"
    await db_session.refresh(first)
    assert first.phone_e164 is None


async def test_cooldown_and_daily_global_budget_count_committed_claims(
    db_session, outbox, monkeypatch
):
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert caught.value.code == "phone_rate_limited"
    await db_session.rollback()
    monkeypatch.setattr(get_settings(), "sms_global_daily_limit", 1)
    other = await account(db_session, "second@example.com")
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, other, "+79991234568", ip_address="other")
    assert caught.value.code == "phone_rate_limited"
    assert len(outbox) == 1


async def test_ambiguous_send_claim_is_durable_before_sms_and_can_verify(
    db_session, outbox, monkeypatch
):
    user = await account(db_session)
    captured = []

    async def timeout(number, code):
        captured.append(code)
        # The claim is committed and visible before the provider is invoked.
        async with async_sessionmaker(db_session.bind)() as observer:
            assert (await observer.scalar(select(PhoneChallenge.id))) is not None
        raise SmsDeliveryAmbiguous()

    monkeypatch.setattr(phone, "send_verification_sms", timeout)
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert caught.value.code == "phone_delivery_unavailable"
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    assert claim.delivery_status == "ambiguous"
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert caught.value.code == "phone_rate_limited"
    await db_session.rollback()
    await db_session.refresh(user)
    await phone.verify_phone_code(db_session, user, captured[0])
    assert user.phone_e164 == NUMBER


async def test_verified_phone_unique_constraint_rejects_duplicate_owner(db_session):
    now = datetime.now(UTC)
    first = User(email="a@example.com", phone_e164=NUMBER, phone_verified_at=now)
    second = User(email="b@example.com", phone_e164=NUMBER, phone_verified_at=now)
    db_session.add_all([first, second])
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_concurrent_send_claims_share_one_durable_phone_cooldown(
    db_session, test_engine, outbox,
):
    first = await account(db_session)
    second = await account(db_session, "concurrent@example.com")
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def request(user_id):
        async with factory() as session:
            user = await session.get(User, user_id)
            try:
                await phone.request_phone_code(session, user, NUMBER, ip_address="qa")
                return "accepted"
            except ApiError as exc:
                return exc.code

    results = await asyncio.gather(request(first.id), request(second.id))
    assert sorted(results) == ["accepted", "phone_rate_limited"]
    assert len(outbox) == 1


async def test_concurrent_code_consumers_bind_phone_exactly_once(db_session, test_engine, outbox):
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def consume():
        async with factory() as session:
            owner = await session.get(User, user.id)
            try:
                await phone.verify_phone_code(session, owner, outbox[0][1])
                return "verified"
            except ApiError as exc:
                return exc.code

    results = await asyncio.gather(consume(), consume())
    assert sorted(results) == ["code_invalid", "verified"]


async def test_expired_claim_retention_preserves_recent_cost_admission(db_session, outbox):
    user = await account(db_session)
    now = datetime.now(UTC)
    claims = [PhoneChallenge(
        user_id=user.id, session_version=0, phone_e164=NUMBER,
        code_hash="private-hash", ip_hash="private-ip", delivery_status="failed",
        created_at=now - age, expires_at=now - age + timedelta(minutes=10),
    ) for age in (timedelta(days=8), timedelta(hours=12))]
    db_session.add_all(claims)
    await db_session.commit()
    old_id, recent_id = [claim.id for claim in claims]
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    remaining = set(await db_session.scalars(select(PhoneChallenge.id)))
    assert old_id not in remaining
    assert recent_id in remaining


async def test_provider_rejection_is_durable_counts_budget_and_cannot_confirm(
    db_session, outbox, monkeypatch,
):
    user = await account(db_session)
    captured = []

    async def rejected(_number, code):
        captured.append(code)
        raise SmsDeliveryFailed("SMS delivery rejected")

    monkeypatch.setattr(phone, "send_verification_sms", rejected)
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert caught.value.code == "phone_delivery_unavailable"
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    assert claim.delivery_status == "failed"
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, user, captured[0])
    assert caught.value.code == "code_invalid"
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert caught.value.code == "phone_rate_limited"
    assert len(captured) == 1


@pytest.mark.parametrize("scope", ["user", "phone", "ip", "global"])
async def test_each_daily_budget_independently_blocks_another_paid_send(
    db_session, outbox, monkeypatch, scope,
):
    first = await account(db_session)
    second = await account(db_session, "other-budget@example.com")
    await phone.request_phone_code(db_session, first, NUMBER, ip_address="same-ip")
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    claim.created_at -= timedelta(seconds=61)
    await db_session.commit()
    monkeypatch.setattr(get_settings(), f"sms_{scope}_daily_limit", 1)
    user = first if scope == "user" else second
    number = NUMBER if scope == "phone" else "+79991234568"
    ip = "same-ip" if scope == "ip" else "different-ip"
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, number, ip_address=ip)
    assert caught.value.code == "phone_rate_limited"
    assert len(outbox) == 1


async def test_permitted_resend_invalidates_old_code_and_confirms_only_new_code(
    db_session, outbox, monkeypatch,
):
    values = iter([123456, 654321])
    monkeypatch.setattr(phone.secrets, "randbelow", lambda _maximum: next(values))
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    claim = (await db_session.scalars(select(PhoneChallenge))).one()
    claim.created_at -= timedelta(seconds=61)
    await db_session.commit()
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    assert [code for _, code in outbox] == ["123456", "654321"]
    with pytest.raises(ApiError) as caught:
        await phone.verify_phone_code(db_session, user, "123456")
    assert caught.value.code == "code_invalid"
    await phone.verify_phone_code(db_session, user, "654321")
    assert user.phone_e164 == NUMBER


async def test_verified_same_number_never_sends_again_and_change_is_rejected(db_session, outbox):
    user = await account(db_session)
    await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa")
    await phone.verify_phone_code(db_session, user, outbox[0][1])
    assert (await phone.request_phone_code(db_session, user, NUMBER, ip_address="qa"))["accepted"]
    with pytest.raises(ApiError) as caught:
        await phone.request_phone_code(db_session, user, "+79991234568", ip_address="qa")
    assert caught.value.code == "forbidden"
    assert len(outbox) == 1
