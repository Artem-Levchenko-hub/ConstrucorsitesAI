"""Bound auth retries without inferring a cause from earlier success.

An unexplained refusal after success in the same run permits bounded retries.
An explicit key block or a first-call auth refusal stops immediately.
The user receives the provider's sanitized explanation and actionable guidance.
"""

from __future__ import annotations

import httpx
import pytest

from yleum_api.services import agent_native

_URL = "https://gateway.test/v1/messages"
_OK = {"content": [{"type": "text", "text": "ok"}]}


@pytest.fixture(autouse=True)
def _forget_previous_runs() -> None:
    agent_native._RUNS_WITH_A_LIVE_KEY.clear()


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(agent_native.asyncio, "sleep", fake_sleep)


@pytest.mark.asyncio
async def test_a_refusal_after_a_success_is_asked_again() -> None:
    """Главное: живой случай 5fdae5f1 больше не выбрасывает работу прогона."""
    replies = [httpx.Response(200, json=_OK), httpx.Response(401, text="unauthorized")]
    replies += [httpx.Response(200, json=_OK)]
    seen: list[int] = []

    def reply(_request: httpx.Request) -> httpx.Response:
        response = replies[min(len(seen), len(replies) - 1)]
        seen.append(response.status_code)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        first = await agent_native._call_messages(client, _URL, [], "s", run_id="run-1")
        second = await agent_native._call_messages(client, _URL, [], "s", run_id="run-1")

    assert first["content"][0]["text"] == "ok"
    assert second["content"][0]["text"] == "ok", "отказ после успеха снова убил вызов"
    assert 401 in seen, "проверка стала бы пустой: отказа не было вовсе"


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

    assert str(failure.value).startswith("PROVIDER_AUTH_FAILED")
    assert "проверьте блокировку" in str(failure.value)
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
    assert message.startswith("PROVIDER_AUTH_FAILED")
    assert "ключ раньше работал" in message
    assert "Проверьте статус и разрешения ключа" in message
    assert "дело скорее" not in message
    # Первый вызов + сам отказ + оговоренные переспросы: молотилки быть не должно.
    assert len(attempts) <= 2 + agent_native._AUTH_RETRIES_AFTER_SUCCESS, len(attempts)


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

    assert "проверьте блокировку" in str(failure.value)
    assert len(attempts) == 2, f"чужой успех не должен разрешать повторы: {len(attempts)}"


@pytest.mark.asyncio
async def test_the_refusal_carries_what_the_provider_actually_said() -> None:
    """Прогон c3ca1587 умер за минуту, и понять причину было нельзя.

    «Провайдер отклонил ключ доступа» — это НАША формулировка; сам провайдер
    отвечает по-разному: неверный ключ, исчерпанный баланс, заблокированный
    аккаунт, слишком много запросов. Шлюз этот ответ сохраняет и отдаёт дальше,
    а платформа его выбрасывала — владельца отправляли проверять ключ, не
    сказав, что именно с ним не так.
    """

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Insufficient balance for this request"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-say")

    assert "Insufficient balance" in str(failure.value)


@pytest.mark.asyncio
async def test_a_plain_text_answer_is_carried_too() -> None:
    """Не всякий отказ приходит разобранным JSON — например, ответ от прокси."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="<html>401 Unauthorized</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-html")

    assert "401 Unauthorized" in str(failure.value)


@pytest.mark.asyncio
async def test_anything_key_shaped_never_leaves_the_platform() -> None:
    """Провайдер иногда возвращает сам ключ — наружу он уйти не должен."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid key sk-abcdef0123456789abcdef0123456789"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-secret")

    message = str(failure.value)
    assert "abcdef0123456789" not in message, message
    assert "Invalid key" in message, "вместе с ключом вырезали и объяснение"


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("previous_success", [False, True])
@pytest.mark.asyncio
async def test_explicit_key_block_stops_without_retry_even_after_success(
    status: int, previous_success: bool,
) -> None:
    attempts: list[int] = []
    complaint = "Authentication Error, Key is blocked. Update via /key/unblock if admin."

    def reply(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if previous_success and len(attempts) == 1:
            return httpx.Response(200, json=_OK)
        if status == 401:
            return httpx.Response(status, json={"error": {"message": complaint}})
        return httpx.Response(status, text=complaint)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        if previous_success:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-blocked")
        with pytest.raises(RuntimeError) as failure:
            await agent_native._call_messages(client, _URL, [], "s", run_id="run-blocked")

    message = str(failure.value)
    assert len(attempts) == 1 + int(previous_success)
    assert message.startswith("PROVIDER_AUTH_FAILED")
    assert "Разблокируйте" in message
    assert "повторите запрос" not in message
    assert "Key is blocked" in message
