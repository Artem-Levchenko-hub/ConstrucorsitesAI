"""Green SMS HTTPS transport with one paid request and a redacted OTP history."""

from uuid import UUID

import httpx

from yleum_api.core.config import get_settings
from yleum_api.services.sms_delivery import SmsDeliveryAmbiguous, SmsDeliveryFailed


def sms_delivery_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.greensms_token
        and settings.greensms_token.get_secret_value().strip()
        and settings.greensms_sender.strip()
    )


def _receipt(response: httpx.Response) -> str:
    if 400 <= response.status_code < 500 and response.status_code != 408:
        raise SmsDeliveryFailed("SMS delivery rejected")
    if response.status_code != 200:
        raise SmsDeliveryAmbiguous("SMS delivery outcome is unknown")
    try:
        body = response.json()
        if not isinstance(body, dict) or "error" in body:
            raise ValueError
        message_id = body.get("request_id")
        if not isinstance(message_id, str) or len(message_id) != 36:
            raise ValueError
        return str(UUID(message_id))
    except (ValueError, TypeError):
        raise SmsDeliveryAmbiguous("SMS delivery receipt is invalid") from None


async def send_verification_sms(
    phone: str, code: str, *, client: httpx.AsyncClient | None = None,
) -> str:
    if not sms_delivery_configured():
        raise SmsDeliveryFailed("SMS delivery is not configured")
    settings = get_settings()
    assert settings.greensms_token
    if client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as owned:
            return await send_verification_sms(phone, code, client=owned)
    try:
        response = await client.post(
            "https://api3.greensms.ru/sms/send",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + settings.greensms_token.get_secret_value().strip(),
            },
            data={
                "to": phone.removeprefix("+"),
                "from": settings.greensms_sender.strip(),
                "txt": f"Ваш код подтверждения: {code}",
                "hidden": code,
            },
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise SmsDeliveryAmbiguous("SMS delivery outcome is unknown") from None
    return _receipt(response)
