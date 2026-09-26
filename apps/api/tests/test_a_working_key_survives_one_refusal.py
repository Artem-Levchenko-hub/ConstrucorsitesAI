"""Ключ, который только что работал, не должен объявляться отклонённым.

26.09.2026, прод, прогон 5fdae5f1 (адаптивный откат). Прогон отработал
сорок шесть минут, сделал 33 успешных обращения к модели, и через 23 секунды
после последнего успеха получил от провайдера 401. Платформа объявила ключ
отклонённым и выбросила всю работу. Накануне то же самое: прогон 0a058f14 —
64 успешных обращения, отказ через 4 секунды после последнего.

Рядом с отказом стоял комментарий: «повтор тут не поможет, поэтому падаем
быстро». Для по-настоящему запрещённого ключа это верно (прогон e4799d09: ни
одного успешного обращения, ключ действительно не работал). Но ключ, который
секунду назад отвечал 200, отказом себя не опровергает — так ведёт себя
провайдер, а не ключ.

Поэтому различаем не код ответа, а то, работал ли ключ В ЭТОМ ЗАПУСКЕ:
— работал → переспрашиваем несколько раз, и текст отказа честно говорит, что
  дело скорее в провайдере;
— не работал ни разу → падаем быстро, как и раньше, и отправляем владельца
  проверять ключ.
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
async def test_the_refusal_stops_asking_and_says_who_is_to_blame() -> None:
    """Если провайдер отказывает подряд — сдаёмся, но не обвиняем ключ зря."""
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
    assert "дело скорее" in message and "в провайдере" in message
    assert "проверьте блокировку" not in message, "владельца зря послали проверять ключ"
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
