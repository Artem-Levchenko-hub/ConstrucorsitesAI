"""Phone ownership is explicit, one time, and independent of email verification."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User


@pytest.mark.parametrize("raw", ["+7 (999) 123-45-67", "89991234567", "79991234567"])
def test_normalizes_only_supported_russian_mobile_numbers(raw):
    from yleum_api.services.phone_verification import normalize_phone

    assert normalize_phone(raw) == "+79991234567"


@pytest.mark.parametrize(
    "raw", ["+12025550123", "9991234567", "+77271234567", "x79991234567", "７9991234567"]
)
def test_rejects_unsupported_or_malformed_phone_numbers(raw):
    from yleum_api.services.phone_verification import normalize_phone

    with pytest.raises(ApiError) as caught:
        normalize_phone(raw)
    assert caught.value.code == "phone_invalid"


def test_otp_hash_binds_account_challenge_and_code():
    from yleum_api.services.phone_verification import otp_hash

    user_id, challenge_id = uuid4(), uuid4()
    digest = otp_hash(user_id, challenge_id, "123456")
    assert len(digest) == 64
    assert digest != otp_hash(uuid4(), challenge_id, "123456")
    assert digest != otp_hash(user_id, uuid4(), "123456")
    assert digest != otp_hash(user_id, challenge_id, "123457")


def test_phone_gate_is_explicit_fail_closed_and_does_not_trust_email(monkeypatch):
    from yleum_api.services.phone_verification import phone_verification_required

    settings = get_settings()
    monkeypatch.setattr(settings, "phone_verification_enabled", True)
    monkeypatch.setattr(settings, "smsaero_email", None)
    user = User(email="legacy@example.com", email_verified_at=datetime.now(UTC))
    assert phone_verification_required(user) is True
    monkeypatch.setattr(settings, "smsaero_email", "qa@example.com")
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "smsaero_api_key", SecretStr("qa-key"))
    assert phone_verification_required(user) is True
    user.phone_e164 = "+79991234567"
    assert phone_verification_required(user) is True
    user.phone_verified_at = datetime.now(UTC)
    assert phone_verification_required(user) is False


async def test_smsaero_posts_once_using_basic_auth_and_form_without_url_secrets(monkeypatch):
    from pydantic import SecretStr

    from yleum_api.services import smsaero

    settings = get_settings()
    monkeypatch.setattr(settings, "smsaero_email", "qa@example.com")
    monkeypatch.setattr(settings, "smsaero_api_key", SecretStr("qa-key"))
    monkeypatch.setattr(settings, "smsaero_sign", "SMS Aero")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url == "https://gate.smsaero.ru/v2/sms/send"
        assert request.method == "POST"
        assert request.headers["authorization"].startswith("Basic ")
        assert b"number=79991234567" in request.content
        assert b"text=" in request.content
        return httpx.Response(200, json={"success": True, "data": [{"id": 42}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await smsaero.send_verification_sms("+79991234567", "123456", client=client) == "42"
    assert len(requests) == 1


async def test_smsaero_timeout_is_ambiguous_and_never_retried(monkeypatch):
    from pydantic import SecretStr

    from yleum_api.services import smsaero

    settings = get_settings()
    monkeypatch.setattr(settings, "smsaero_email", "qa@example.com")
    monkeypatch.setattr(settings, "smsaero_api_key", SecretStr("qa-key"))
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(smsaero.SmsDeliveryAmbiguous):
            await smsaero.send_verification_sms("+79991234567", "123456", client=client)
    assert count == 1
