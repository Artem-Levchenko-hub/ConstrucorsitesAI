"""SMS Aero HTTPS transport. Never retry a paid send or retain its response body."""

import httpx

from yleum_api.core.config import get_settings
from yleum_api.services.sms_delivery import SmsDeliveryAmbiguous as SmsDeliveryAmbiguous
from yleum_api.services.sms_delivery import SmsDeliveryFailed as SmsDeliveryFailed


def sms_delivery_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.smsaero_email
        and settings.smsaero_email.strip()
        and settings.smsaero_api_key
        and settings.smsaero_api_key.get_secret_value().strip()
        and settings.smsaero_sign.strip()
    )


async def send_verification_sms(
    phone: str,
    code: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    if not sms_delivery_configured():
        raise SmsDeliveryFailed("SMS delivery is not configured")
    settings = get_settings()
    assert settings.smsaero_email and settings.smsaero_api_key
    if client is None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as owned:
            return await send_verification_sms(phone, code, client=owned)
    try:
        response = await client.post(
            "https://gate.smsaero.ru/v2/sms/send",
            auth=httpx.BasicAuth(
                settings.smsaero_email, settings.smsaero_api_key.get_secret_value()
            ),
            headers={"Accept": "application/json"},
            data={
                "number": phone.removeprefix("+"),
                "sign": settings.smsaero_sign,
                "text": f"Yleum: код {code}. Никому не сообщайте. Действует 10 минут.",
            },
        )
    except httpx.HTTPError:
        # Transport failures may happen after the provider accepted the message.
        raise SmsDeliveryAmbiguous("SMS delivery outcome is unknown") from None
    if response.status_code >= 500:
        raise SmsDeliveryAmbiguous("SMS delivery outcome is unknown")
    if response.status_code != 200:
        raise SmsDeliveryFailed("SMS delivery rejected")
    try:
        body = response.json()
        if not isinstance(body, dict) or body.get("success") is not True:
            raise SmsDeliveryFailed("SMS delivery rejected")
        data = body.get("data")
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        message_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(message_id, (int, str)) or isinstance(message_id, bool):
            raise SmsDeliveryAmbiguous("SMS delivery receipt is invalid")
        return str(message_id)
    except (ValueError, TypeError):
        raise SmsDeliveryAmbiguous("SMS delivery receipt is invalid") from None
