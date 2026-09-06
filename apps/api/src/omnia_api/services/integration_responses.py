"""Validate provider contracts before exposing a successful business operation."""

from typing import Any
from urllib.parse import urlparse

import httpx

from omnia_api.core.errors import ApiError
from omnia_api.schemas.integration_runtime import RuntimePaymentPublic


def invalid_response() -> ApiError:
    return ApiError("integration_response_invalid", "Сервис вернул некорректный ответ", 502)


def response_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise invalid_response() from exc
    if not isinstance(value, dict):
        raise invalid_response()
    if value.get("error"):
        # Never echo provider error text: it can contain credentials or user data.
        raise ApiError(
            "integration_request_rejected",
            "Сервис отклонил операцию. Проверьте права подключения.",
            422,
        )
    return value


def payment_response(value: dict[str, Any]) -> RuntimePaymentPublic:
    payment_id = value.get("id")
    state = value.get("status")
    if (
        not isinstance(payment_id, str)
        or not payment_id.strip()
        or state
        not in (
            "pending",
            "waiting_for_capture",
            "succeeded",
            "canceled",
        )
    ):
        raise invalid_response()
    confirmation = value.get("confirmation")
    if confirmation is not None and not isinstance(confirmation, dict):
        raise invalid_response()
    url = (confirmation or {}).get("confirmation_url")
    if url is not None:
        if not isinstance(url, str):
            raise invalid_response()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise invalid_response()
    return RuntimePaymentPublic(id=payment_id, status=state, confirmation_url=url)


def lead_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise invalid_response()
    identifier = str(value)
    if not identifier.isdecimal() or int(identifier) <= 0:
        raise invalid_response()
    return identifier
