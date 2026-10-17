"""Access refusals stay terminal without guessing from earlier success.

Every 401/403 stops this provider request. Structured gateway categories give
fixed factual guidance; free-form provider complaints and keys never escape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation import provider_attempts

_URL = "https://gateway.test/v1/messages"
_OK = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(_delay: float) -> None:
        pytest.fail("access refusal must not back off and replay a provider POST")

    monkeypatch.setattr(agent_native.asyncio, "sleep", fake_sleep)
    # Synthetic attribution labels exercise the HTTP refusal policy, not SQL admission.
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_a_refusal_after_a_success_is_not_replayed() -> None:
    """Prior success cannot prove that replaying the refusal will repair access."""
    replies = [httpx.Response(200, json=_OK), httpx.Response(401, text="unauthorized")]
    replies += [httpx.Response(200, json=_OK)]
    seen: list[int] = []

    def reply(_request: httpx.Request) -> httpx.Response:
        response = replies[min(len(seen), len(replies) - 1)]
        seen.append(response.status_code)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        first = await agent_native._call_messages(client, _URL, [], "s", run_id="run-1")
        with pytest.raises(RuntimeError, match="provider_http_401"):
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-1")

    assert first["content"][0]["text"] == "ok"
    assert seen == [200, 401], "the offered third reply must not cause a blind retry"


@pytest.mark.asyncio
async def test_a_key_that_never_worked_still_fails_fast() -> None:
    """Прогон e4799d09: ключ действительно не работал — молотить незачем."""
    attempts: list[int] = []

    def reply(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, text="unauthorized")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-2")

    assert str(failure.value).startswith("provider_http_401:")
    assert "проверить доступ и средства" in str(failure.value)
    assert "недостат" not in str(failure.value).casefold()
    assert len(attempts) == 1, f"ключ не работал ни разу — повторять нечего: {len(attempts)}"


@pytest.mark.asyncio
async def test_repeated_refusal_stops_with_factual_guidance() -> None:
    """Earlier success does not establish why access is now refused."""
    attempts: list[int] = []

    def reply(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(200, json=_OK)
        return httpx.Response(401, text="unauthorized")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await agent_native._call_messages(client, _URL, [], "s", run_id="run-3")
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-3")

    message = str(failure.value)
    assert message.startswith("provider_http_401:")
    assert "проверить доступ и средства" in message
    assert "ключ раньше работал" not in message
    assert "дело скорее" not in message
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_a_success_in_one_run_does_not_vouch_for_another() -> None:
    """Иначе чужой удачный вызов заставил бы молотить по чужому запрещённому ключу."""
    attempts: list[int] = []

    def reply(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, json=_OK) if len(attempts) == 1 else httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await agent_native._call_messages(client, _URL, [], "s", run_id="run-good")
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-other")

    assert str(failure.value).startswith("provider_http_401:")
    assert len(attempts) == 2, f"чужой успех не должен разрешать повторы: {len(attempts)}"


@pytest.mark.asyncio
async def test_structured_refusal_category_gives_safe_factual_guidance() -> None:
    """Only explicit gateway evidence may distinguish funds from access."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {
                "message": "Insufficient balance for this request private-provider-detail",
                "details": {"upstream_http_status": 401, "provider_refusal_category": "funds"},
            }}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-say")

    message = str(failure.value)
    assert message.startswith("provider_refusal_funds:")
    assert "Провайдер модели сообщил о недостатке средств" in message
    assert "Insufficient balance" not in message and "private-provider-detail" not in message


@pytest.mark.asyncio
async def test_a_plain_text_refusal_stays_generic_without_echoing_its_body() -> None:
    """Unstructured proxy errors cannot establish access or funds evidence."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="<html>401 Unauthorized</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-html")

    assert str(failure.value).startswith("provider_http_401:")
    assert "проверить доступ и средства" in str(failure.value)
    assert "401 Unauthorized" not in str(failure.value)


@pytest.mark.asyncio
async def test_anything_key_shaped_never_leaves_the_platform() -> None:
    """Провайдер иногда возвращает сам ключ — наружу он уйти не должен."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {
                "message": "Invalid key sk-abcdef0123456789abcdef0123456789",
                "details": {"upstream_http_status": 401, "provider_refusal_category": "access"},
            }},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-secret")

    message = str(failure.value)
    assert "abcdef0123456789" not in message, message
    assert "Invalid key" not in message
    assert message.startswith("provider_refusal_access:")
    assert "Провайдер модели отклонил доступ к запросу" in message


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("previous_success", [False, True])
@pytest.mark.asyncio
async def test_structured_access_refusal_stops_without_retry_even_after_success(
    status: int, previous_success: bool,
) -> None:
    attempts: list[int] = []
    complaint = "Authentication Error, Key is blocked. Update via /key/unblock if admin."

    def reply(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if previous_success and len(attempts) == 1:
            return httpx.Response(200, json=_OK)
        return httpx.Response(status, json={"error": {
            "message": complaint,
            "details": {"upstream_http_status": status, "provider_refusal_category": "access"},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        if previous_success:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-blocked")
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-blocked")

    message = str(failure.value)
    assert len(attempts) == 1 + int(previous_success)
    assert message.startswith("provider_refusal_access:")
    assert "Провайдер модели отклонил доступ к запросу" in message
    assert "Автоматический повтор остановлен" in message
    assert "повторите запрос" not in message
    assert "Key is blocked" not in message and "/key/unblock" not in message
