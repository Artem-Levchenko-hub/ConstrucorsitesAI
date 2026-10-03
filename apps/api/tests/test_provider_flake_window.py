"""Сборка не должна умирать от минутной просадки провайдера моделей.

22.09.2026, 07:29 МСК: внешний провайдер `api.llmgw.ru` отдавал 502 Bad Gateway
51 секунду. Этого хватило, чтобы сборка пользователя упала целиком с
`PROVIDER_UNAVAILABLE`, хотя через минуту провайдер снова отвечал 200.

Разбор показал расхождение кода с собственным комментарием: рядом с паузами
написано, что экспоненциальный откат «переживает многоминутное окно сбоя
(~3.5 минуты)», а бюджет попыток стоял 3 — то есть примерно 50 секунд. Тесты
ниже закрепляют не константу, а само свойство: сколько длится окно, что
короткий сбой переживается, и что последняя пауза не тратится впустую.
"""

from __future__ import annotations

import httpx
import pytest

from yleum_api.services import agent_native

_URL = "https://gateway.test/v1/messages"

# Столько длился живой сбой 22.09.2026. Окно повторов обязано быть заметно шире:
# иначе следующая такая же просадка снова снесёт чужую сборку.
_OBSERVED_FLAKE_SECONDS = 51.0
_PROMISED_WINDOW_SECONDS = 150.0


def _record_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(agent_native.asyncio, "sleep", fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_a_flake_as_long_as_the_live_one_no_longer_kills_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ровно живой случай: 502 дольше минуты, потом провайдер вернулся."""
    slept = _record_sleeps(monkeypatch)
    elapsed = {"s": 0.0}
    attempts: list[int] = []

    def reply(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        # Каждая попытка сама по себе тратит время сети, и к ней добавляется пауза.
        elapsed["s"] += 5.0 + (slept[-1] if slept else 0.0)
        if elapsed["s"] < _OBSERVED_FLAKE_SECONDS:
            return httpx.Response(502, text="<html>502 Bad Gateway</html>")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        body = await agent_native._call_messages(client, _URL, [], "s")

    assert body["content"][0]["text"] == "ok"
    assert len(attempts) > 1, "сбой обязан быть пережит повтором, а не пройти с первого раза"


@pytest.mark.asyncio
async def test_the_retry_window_is_as_wide_as_the_code_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обещание в комментарии проверяется арифметикой, а не на веру."""
    slept = _record_sleeps(monkeypatch)

    def always_502(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async with httpx.AsyncClient(transport=httpx.MockTransport(always_502)) as client:
        with pytest.raises(httpx.HTTPError):
            await agent_native._call_messages(client, _URL, [], "s")

    assert sum(slept) >= _PROMISED_WINDOW_SECONDS, (
        f"окно повторов {sum(slept):.0f}s меньше обещанных "
        f"{_PROMISED_WINDOW_SECONDS:.0f}s — живой сбой снова снесёт сборку"
    )


@pytest.mark.asyncio
async def test_giving_up_is_not_delayed_by_a_pause_nobody_waits_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """После последней попытки спать бессмысленно: ответа уже никто не ждёт."""
    slept = _record_sleeps(monkeypatch)
    attempts: list[int] = []

    def always_502(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(502, text="bad gateway")

    async with httpx.AsyncClient(transport=httpx.MockTransport(always_502)) as client:
        with pytest.raises(httpx.HTTPError):
            await agent_native._call_messages(client, _URL, [], "s")

    assert len(slept) == len(attempts) - 1


@pytest.mark.asyncio
async def test_the_backoff_grows_and_stays_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Паузы растут (чтобы не долбить лежащего) и упираются в потолок."""
    slept = _record_sleeps(monkeypatch)

    def always_502(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async with httpx.AsyncClient(transport=httpx.MockTransport(always_502)) as client:
        with pytest.raises(httpx.HTTPError):
            await agent_native._call_messages(client, _URL, [], "s")

    assert slept == sorted(slept), "откат обязан расти, а не скакать"
    assert max(slept) <= 45.0, "одна пауза не должна съедать минуту дедлайна"


@pytest.mark.asyncio
@pytest.mark.parametrize("window_seconds, expected_attempts", [(210.0, 2), (270.0, 3)])
async def test_a_provider_that_hangs_cannot_eat_the_whole_deadline(
    monkeypatch: pytest.MonkeyPatch,
    window_seconds: float,
    expected_attempts: int,
) -> None:
    """Второй бок бюджета: потолок не по числу попыток, а по реальному времени.

    Провайдер, который принимает соединение и молчит до таймаута, укладывается в
    отпущенные попытки, но каждая стоит минуты. Без потолка семь таких попыток
    съели бы весь дедлайн генерации на одном шаге.
    """
    clock = {"now": 0.0}
    monkeypatch.setattr(agent_native, "_CALL_RETRY_WINDOW_S", window_seconds)

    async def elapsed_sleep(delay: float) -> None:
        clock["now"] += delay

    monkeypatch.setattr(agent_native.asyncio, "sleep", elapsed_sleep)

    class _FakeLoop:
        def time(self) -> float:
            return clock["now"]

    monkeypatch.setattr(agent_native.asyncio, "get_running_loop", lambda: _FakeLoop())
    attempts: list[int] = []
    request_budgets: list[float] = []

    def hangs_then_fails(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        budget = request.extensions["timeout"]["read"]
        request_budgets.append(budget)
        # MockTransport не применяет timeout сам: моделируем ожидание строго
        # в пределах бюджета, который передал настоящий клиент.
        clock["now"] += min(120.0, budget)
        return httpx.Response(502, text="bad gateway")

    async with httpx.AsyncClient(transport=httpx.MockTransport(hangs_then_fails)) as client:
        with pytest.raises(httpx.HTTPError):
            await agent_native._call_messages(client, _URL, [], "s")

    assert len(attempts) == expected_attempts
    assert clock["now"] == window_seconds
    assert request_budgets == sorted(request_budgets, reverse=True)
    assert request_budgets[-1] < 120.0, "последний запрос ограничен оставшимся временем"


@pytest.mark.asyncio
async def test_a_dead_key_still_fails_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Расширенное окно не должно превратить мгновенные отказы в трёхминутные.

    401/403/402 повтором не лечатся: ключ заблокирован или баланс исчерпан.
    """
    slept = _record_sleeps(monkeypatch)

    for status, marker in (
        (401, "PROVIDER_AUTH_FAILED"),
        (403, "PROVIDER_AUTH_FAILED"),
        (402, "PAYMENT_REQUIRED"),
    ):
        attempts: list[int] = []

        def reply(request: httpx.Request, code: int = status) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(code, json={"error": {"message": "no"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(RuntimeError, match=marker):
                await agent_native._call_messages(client, _URL, [], "s")

        assert attempts == [1], f"{status} повторять нельзя"

    assert slept == [], "мгновенный отказ не ждёт откатов"
