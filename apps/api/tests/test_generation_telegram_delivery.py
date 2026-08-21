from __future__ import annotations

import json
from importlib import import_module
from types import ModuleType
from uuid import UUID

import httpx
import pytest


def _delivery_module() -> ModuleType:
    try:
        return import_module("omnia_api.services.generation_telegram_delivery")
    except ModuleNotFoundError:
        pytest.fail("generation Telegram delivery service is missing", pytrace=False)


def test_start_delivery_uses_exact_user_text_and_sanitizes_only_boundary_data() -> None:
    delivery = _delivery_module()
    run_id = UUID("a1b2c3d4-1111-2222-3333-444455556666")
    telegram_token = "12345678:" + "A" * 30
    user_text = (
        "Сделай лендинг для owner@example.com\n"
        "Референс: https://example.com/look?q=blue\n"
        f"Служебный токен {telegram_token}"
    )

    payload = delivery.build_start_delivery(
        run_id=run_id,
        mode="build",
        project_name="  ACME\nStudio\x00  ",
        user_text=user_text,
    )

    assert payload.text == (
        "🟡 BUILD · Проект «ACME Studio» · #a1b2c3d4\n"
        "Промпт:\n"
        "Сделай лендинг для owner@example.com\n"
        "Референс: https://example.com/look?q=blue\n"
        "Служебный токен [CREDENTIAL REDACTED]"
    )
    assert payload.prompt_document is None
    assert payload.prompt_filename is None


def test_long_start_delivery_keeps_complete_redacted_prompt_in_memory_document() -> None:
    delivery = _delivery_module()
    run_id = UUID("11223344-1111-2222-3333-444455556666")
    user_text = "точный пользовательский текст\n" * 220

    payload = delivery.build_start_delivery(
        run_id=run_id,
        mode="edit",
        project_name="Каталог",
        user_text=user_text,
    )

    assert len(payload.text) <= 3600
    assert "Полный промпт приложен файлом." in payload.text
    assert payload.prompt_document == user_text.encode("utf-8")
    assert payload.prompt_filename == "generation-11223344-prompt.txt"


def test_error_sanitizer_removes_external_identifiers_and_bounds_output() -> None:
    delivery = _delivery_module()
    telegram_token = "12345678:" + "B" * 30
    raw = (
        "RuntimeError: upstream owner@example.com failed at "
        "https://storage.example.com/private/object.png?signature=secret#fragment\n"
        f"token={telegram_token}\x00 "
        + "x" * 800
    )

    safe = delivery.sanitize_error(raw)

    assert "owner@example.com" not in safe
    assert "storage.example.com" not in safe
    assert telegram_token not in safe
    assert "\n" not in safe
    assert "\x00" not in safe
    assert "[EMAIL REDACTED]" in safe
    assert "[URL REDACTED]" in safe
    assert "[CREDENTIAL REDACTED]" in safe
    assert len(safe) == 500
    assert safe.endswith("…")


@pytest.mark.parametrize(
    ("outcome", "mode", "stage", "error", "expected"),
    [
        (
            "completed",
            "build",
            "preview",
            None,
            "✅ BUILD завершён · 08:42 · #a1b2c3d4",
        ),
        (
            "completed_no_snapshot",
            "edit",
            "snapshot",
            None,
            "✅ EDIT завершён без нового снимка · 00:09 · #a1b2c3d4",
        ),
        (
            "failed",
            "build",
            "writer",
            "RuntimeError: provider unavailable",
            "❌ BUILD упал · этап: writer · 03:17 · #a1b2c3d4\n"
            "Ошибка: RuntimeError: provider unavailable",
        ),
        (
            "cancelled",
            "edit",
            "routing",
            None,
            "⚪ EDIT отменён пользователем · 00:12 · #a1b2c3d4",
        ),
        (
            "preview_warning",
            "build",
            "preview",
            None,
            "⚠️ BUILD готов, но preview не получен · 05:00 · #a1b2c3d4\n"
            "Причина: container_unreachable",
        ),
        (
            "late_preview",
            "build",
            "preview",
            None,
            "🖼 Preview появился позже · BUILD · #a1b2c3d4",
        ),
    ],
)
def test_finish_delivery_has_one_bounded_product_message_per_outcome(
    outcome: str,
    mode: str,
    stage: str,
    error: str | None,
    expected: str,
) -> None:
    delivery = _delivery_module()

    actual = delivery.build_finish_text(
        run_id=UUID("a1b2c3d4-1111-2222-3333-444455556666"),
        mode=mode,
        outcome=outcome,
        elapsed_seconds={
            "completed": 522,
            "completed_no_snapshot": 9,
            "failed": 197,
            "cancelled": 12,
            "preview_warning": 300,
            "late_preview": 700,
        }[outcome],
        stage=stage,
        error=error,
        preview_error_code=(
            "container_unreachable" if outcome == "preview_warning" else None
        ),
    )

    assert actual == expected


def test_traceback_is_never_returned_as_an_error_message() -> None:
    delivery = _delivery_module()
    raw = "Traceback (most recent call last):\n  File '/app/x.py', line 3\nValueError: bad"

    assert delivery.sanitize_error(raw) == "[TRACEBACK REDACTED]"


def test_telegram_client_rejects_missing_token_and_non_group_chat() -> None:
    delivery = _delivery_module()

    with pytest.raises(ValueError, match="token"):
        delivery.TelegramBotClient(token="", chat_id=-1001234567890)
    with pytest.raises(ValueError, match="negative"):
        delivery.TelegramBotClient(token="synthetic-token", chat_id=0)
    with pytest.raises(ValueError, match="negative"):
        delivery.TelegramBotClient(token="synthetic-token", chat_id=123)


@pytest.mark.asyncio
async def test_send_message_uses_fixed_origin_chat_reply_and_bounded_timeouts() -> None:
    delivery = _delivery_module()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 731}},
        )

    transport = httpx.MockTransport(handler)
    client = delivery.TelegramBotClient(
        token="synthetic-token",
        chat_id=-1001234567890,
        transport=transport,
    )
    try:
        message_id = await client.send_message("готово", reply_to=91)
    finally:
        await client.aclose()

    assert message_id == 731
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.scheme == "https"
    assert request.url.host == "api.telegram.org"
    assert request.url.path == "/botsynthetic-token/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": -1001234567890,
        "text": "готово",
        "link_preview_options": {"is_disabled": True},
        "reply_parameters": {"message_id": 91},
    }
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 15.0,
        "write": 15.0,
        "pool": 5.0,
    }


@pytest.mark.asyncio
async def test_document_and_photo_are_uploaded_as_threaded_multipart_bytes() -> None:
    delivery = _delivery_module()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 800 + len(requests)}},
        )

    client = delivery.TelegramBotClient(
        token="synthetic-token",
        chat_id=-1001234567890,
        transport=httpx.MockTransport(handler),
    )
    try:
        document_id = await client.send_document(
            b"exact prompt bytes",
            "generation-a1b2c3d4-prompt.txt",
            caption="Полный промпт",
            reply_to=731,
        )
        photo_id = await client.send_photo(
            b"\x89PNG\r\nexact-preview",
            caption="✅ BUILD завершён",
            reply_to=731,
        )
    finally:
        await client.aclose()

    assert (document_id, photo_id) == (801, 802)
    assert [request.url.path for request in requests] == [
        "/botsynthetic-token/sendDocument",
        "/botsynthetic-token/sendPhoto",
    ]
    document_body = requests[0].content
    assert b"exact prompt bytes" in document_body
    assert b'generation-a1b2c3d4-prompt.txt' in document_body
    assert b'name="chat_id"' in document_body
    assert b"-1001234567890" in document_body
    assert b'{"message_id":731}' in document_body
    photo_body = requests[1].content
    assert b"\x89PNG\r\nexact-preview" in photo_body
    assert b'name="photo"' in photo_body
    assert b'{"message_id":731}' in photo_body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "code", "retryable", "retry_after"),
    [
        (
            429,
            {
                "ok": False,
                "description": "do not persist this response body",
                "parameters": {"retry_after": 2000},
            },
            "telegram_rate_limited",
            True,
            900,
        ),
        (
            503,
            {"ok": False, "description": "upstream secret"},
            "telegram_server_error",
            True,
            None,
        ),
        (
            403,
            {"ok": False, "description": "chat secret"},
            "telegram_forbidden",
            False,
            None,
        ),
    ],
)
async def test_telegram_failures_have_fixed_codes_without_response_body(
    status_code: int,
    body: dict[str, object],
    code: str,
    retryable: bool,
    retry_after: int | None,
) -> None:
    delivery = _delivery_module()
    transport = httpx.MockTransport(lambda _request: httpx.Response(status_code, json=body))
    client = delivery.TelegramBotClient(
        token="synthetic-token",
        chat_id=-1001234567890,
        transport=transport,
    )
    try:
        with pytest.raises(delivery.TelegramFailure) as raised:
            await client.send_message("status")
    finally:
        await client.aclose()

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.retry_after_seconds == retry_after
    assert str(raised.value) == code
    assert "secret" not in repr(raised.value)
    assert "response body" not in repr(raised.value)


@pytest.mark.asyncio
async def test_timeout_and_malformed_success_are_reduced_to_safe_fixed_codes() -> None:
    delivery = _delivery_module()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive transport detail", request=request)

    timeout_client = delivery.TelegramBotClient(
        token="synthetic-token",
        chat_id=-1001234567890,
        transport=httpx.MockTransport(timeout_handler),
    )
    try:
        with pytest.raises(delivery.TelegramFailure) as timeout_raised:
            await timeout_client.send_message("status")
    finally:
        await timeout_client.aclose()
    assert timeout_raised.value.code == "telegram_timeout"
    assert timeout_raised.value.retryable is True
    assert "sensitive" not in repr(timeout_raised.value)

    malformed_client = delivery.TelegramBotClient(
        token="synthetic-token",
        chat_id=-1001234567890,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"ok": True, "result": {}})
        ),
    )
    try:
        with pytest.raises(delivery.TelegramFailure) as malformed_raised:
            await malformed_client.send_message("status")
    finally:
        await malformed_client.aclose()
    assert malformed_raised.value.code == "telegram_malformed_response"
    assert malformed_raised.value.retryable is False
