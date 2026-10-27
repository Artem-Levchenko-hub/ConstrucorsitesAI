"""Green SMS sends once, uses private headers, and classifies uncertain outcomes."""

from datetime import UTC, datetime
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from yleum_api.core.config import get_settings


def configure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sms_provider", "greensms")
    monkeypatch.setattr(settings, "greensms_token", SecretStr("qa-green-token"))
    monkeypatch.setattr(settings, "greensms_sender", "GREENSMS")
    return settings


async def test_green_sms_posts_one_private_request_and_masks_otp_in_provider_history(monkeypatch):
    from yleum_api.services.sms_delivery import send_verification_sms, sms_delivery_configured

    configure(monkeypatch)
    assert sms_delivery_configured() is True
    receipt = str(uuid4())
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api3.greensms.ru/sms/send"
        assert request.headers["authorization"] == "Bearer qa-green-token"
        form = parse_qs(request.content.decode())
        assert form["to"] == ["79991234567"]
        assert form["from"] == ["GREENSMS"]
        assert form["hidden"] == ["123456"]
        assert form["txt"] == ["Yleum: код 123456. Никому не сообщайте. Действует 10 минут."]
        assert len(form["txt"][0]) <= 70
        assert "user" not in form and "pass" not in form
        return httpx.Response(200, json={"request_id": receipt})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await send_verification_sms("+79991234567", "123456", client=client) == receipt
    assert len(requests) == 1


@pytest.mark.parametrize("status", [400, 401, 402, 403, 429])
async def test_green_sms_explicit_rejection_is_failed_without_provider_body_leak(
    monkeypatch, status,
):
    from yleum_api.services.sms_delivery import (
        SmsDeliveryAmbiguous,
        SmsDeliveryFailed,
        send_verification_sms,
    )

    configure(monkeypatch)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "private 79991234567 code 123456", "code": 6})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SmsDeliveryFailed) as caught:
            await send_verification_sms("+79991234567", "123456", client=client)
    assert not isinstance(caught.value, SmsDeliveryAmbiguous)
    assert "79991234567" not in str(caught.value)
    assert "123456" not in str(caught.value)
    assert "private" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status,body",
    [
        (500, {}), (503, {"error": "down"}), (408, {}),
        (200, {}), (200, []), (200, {"request_id": None}),
        (200, {"request_id": ""}), (200, {"request_id": True}),
        (200, {"request_id": "invalid"}),
        (200, {"request_id": str(uuid4()), "error": "rejected"}),
        (301, {"request_id": str(uuid4())}),
    ],
)
async def test_green_sms_untrusted_receipt_is_ambiguous_and_never_retried(
    monkeypatch, status, body,
):
    from yleum_api.services.sms_delivery import SmsDeliveryAmbiguous, send_verification_sms

    configure(monkeypatch)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://example.com/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SmsDeliveryAmbiguous):
            await send_verification_sms("+79991234567", "123456", client=client)
    assert len(requests) == 1


@pytest.mark.parametrize("outcome", ["timeout", "bad-json"])
async def test_green_sms_response_loss_remains_ambiguous(monkeypatch, outcome):
    from yleum_api.services.sms_delivery import SmsDeliveryAmbiguous, send_verification_sms

    configure(monkeypatch)
    requests = []

    def respond(request):
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private token 123456", request=request)
        return httpx.Response(200, content=b"not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SmsDeliveryAmbiguous) as caught:
            await send_verification_sms("+79991234567", "123456", client=client)
    assert "123456" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("token,sender", [(None, "GREENSMS"), (" ", "GREENSMS"), ("key", " ")])
async def test_selected_green_sms_does_not_fall_back_to_configured_aero(monkeypatch, token, sender):
    from yleum_api.services.sms_delivery import SmsDeliveryFailed, send_verification_sms

    settings = configure(monkeypatch)
    monkeypatch.setattr(settings, "greensms_token", SecretStr(token) if token is not None else None)
    monkeypatch.setattr(settings, "greensms_sender", sender)
    monkeypatch.setattr(settings, "smsaero_email", "qa@example.com")
    monkeypatch.setattr(settings, "smsaero_api_key", SecretStr("qa-aero"))

    def unexpected(_request):
        pytest.fail("Unconfigured Green SMS must not call any provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(SmsDeliveryFailed):
            await send_verification_sms("+79991234567", "123456", client=client)


def test_account_readiness_uses_selected_green_sms_without_aero(monkeypatch):
    from yleum_api.models.user import User
    from yleum_api.schemas.user import UserPublic

    settings = configure(monkeypatch)
    monkeypatch.setattr(settings, "smsaero_email", None)
    monkeypatch.setattr(settings, "smsaero_api_key", None)
    user = User(id=uuid4(), email="qa@example.com", is_anon=False, status="active", role="user",
                created_at=datetime.now(UTC))
    assert UserPublic.model_validate(user).phone_verification_available is True


def test_sms_provider_settings_reject_unknown_selection():
    from pydantic import ValidationError

    from yleum_api.core.config import Settings

    with pytest.raises(ValidationError, match="sms_provider"):
        Settings(sms_provider="typo-provider")
