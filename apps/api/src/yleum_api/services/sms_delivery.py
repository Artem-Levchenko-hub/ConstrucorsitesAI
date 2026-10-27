"""Select one SMS transport; paid sends never retry or fall back implicitly."""

import httpx

from yleum_api.core.config import get_settings


class SmsDeliveryFailed(Exception):
    """The provider rejected the request or delivery is not configured."""


class SmsDeliveryAmbiguous(SmsDeliveryFailed):
    """The provider may already have accepted the message; do not resend."""


def sms_delivery_configured() -> bool:
    if get_settings().sms_provider == "greensms":
        from yleum_api.services.greensms import sms_delivery_configured as configured
    else:
        from yleum_api.services.smsaero import sms_delivery_configured as configured
    return configured()


async def send_verification_sms(
    phone: str, code: str, *, client: httpx.AsyncClient | None = None,
) -> str:
    if get_settings().sms_provider == "greensms":
        from yleum_api.services.greensms import send_verification_sms as send
    else:
        from yleum_api.services.smsaero import send_verification_sms as send
    return await send(phone, code, client=client)
