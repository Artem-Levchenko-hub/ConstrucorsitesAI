"""Preparation request preserves client choices and truthful MAX/data boundaries."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yleum_api.services import llm_client
from yleum_api.services import prompt_preparation as prep


@pytest.mark.parametrize('description', [
    'Каталог пуст. На первом экране нужна декоративная иллюстрация, без выдуманных товаров.',
    'Без фотографий и декоративной графики. Имя пользователь вводит в форме.',
    'Нужен аватар в профиле, если пользователь разрешённым способом добавил его сам.',
    'Платное составление рациона. До настройки тарифов нужны анкета и доступная история.',
    'Главное действие: Купить подписку на сервис. Работа по подписке, три тарифа. '
    'Каталог пуст. Нужны анкета, рацион, дневник и навигация.',
    'Главное действие: Купить подписку на сервис. Все экраны и анкета доступны '
    'только после подписки; до оплаты только объяснение и покупка.',
    'Все данные сервиса доступны только подписчикам. Публично только объяснение и вход.',
    'Провайдер временно недоступен. Сохрани введённый контекст и навигацию по моим экранам.',
    'Публичный подзаголовок точно «Мини-приложение для пользователей MAX». Не меняй его.',
    pytest.param(
        'Начало задачи. ' + 'я' * 8000 + 'ВАЖНЫЙ СЦЕНАРИЙ В СЕРЕДИНЕ' + 'я' * 8000 + ' Конец.',
        id='long_description_keeps_middle',
    ),
    pytest.param('🙂' * 29_999 + '!', id='unicode_max_description'),
])
async def test_guidance_reaches_the_same_single_request_without_rewriting_owner_choices(
    monkeypatch, description,
):
    preferences = {'tone': 'выразительно', 'fonts': [{'family': 'Onest', 'role': 'body'}]}
    messages = prep.preparation_messages(description, preferences)
    calls = []

    async def complete(actual_messages, model, **kwargs):
        calls.append((actual_messages, model, kwargs))
        return 'Согласованный план'

    monkeypatch.setattr(llm_client, 'complete_chat', complete)
    result = await llm_client.complete_prompt_brief(messages, user_id='qa', project_id='qa')
    assert result == 'Согласованный план'
    assert len(calls) == 1
    actual_messages, _, kwargs = calls[0]
    assert [message['role'] for message in actual_messages] == ['system', 'user']
    assert json.loads(actual_messages[1]['content']) == {
        'description': description, 'preferences': preferences,
    }
    instruction = ' '.join(actual_messages[0]['content'].split())
    assert 'MAX-профиль предоставляет только идентификатор user.id' in instruction
    assert 'не приписывай ему имя, аватар или другие данные' in instruction
    assert 'Явный запрос клиента на такие данные сохрани' in instruction
    assert (
        'подтверждёнными возможностями платформы или разрешённым вводом пользователя' in instruction
    )
    assert 'пустой каталог не запрещает уместные фото, иллюстрации или графику' in instruction
    assert 'Декор не выдаётся за реальные позиции каталога или записи пользователя' in instruction
    assert 'Не изобретай новых запретов' in instruction
    assert 'явные ограничения клиента сохраняй' in instruction
    for required in (
        'исходные данные пользователя, настройку сервиса владельцем '
        'и право на платную операцию',
        'состояние настройки, а не причина автоматически закрывать весь интерфейс и навигацию',
        'Модель работы по подписке и главное действие «Купить подписку на сервис» '
        'сами по себе не запрещают навигацию',
        'заполнение исходного пользовательского контекста до оплаты',
        'только по явному условию владельца или подтверждённому серверному контракту',
        'Сохрани визуальный приоритет покупки',
        'пустой список тарифов не делает её единственным разрешённым шагом',
        'доступ требует подтверждённого сервером права',
        'создание заказа, возврат из оплаты или показ цены не означают оплату',
        'клиент явно ограничил доступ ко всему продукту, сохрани именно эту границу',
        'нейтральный текст платформы не обязан становиться подзаголовком приложения',
        'Первый экран без данных должен иметь ту же продуманную композицию, '
        'иерархию и характер',
    ):
        assert required in instruction
    assert kwargs['stage'] == 'prompt_preparation' and kwargs['free'] is True
    assert kwargs['max_tokens'] is None and kwargs['temperature'] == 0.2


def test_preparation_covers_the_whole_accepted_prompt_contract_without_clipping():
    from yleum_api.schemas.message import PromptRequest

    accepted = 'Начало. ' + 'я' * 14_980 + 'НУЖЕН СВОЙ ЭКРАН'
    accepted += '🙂' * (30_000 - len(accepted))
    assert len(accepted) == 30_000
    assert PromptRequest(prompt=accepted).prompt == accepted
    body = json.loads(prep.preparation_messages(accepted, {})[1]['content'])
    assert body['description'] == accepted
    with pytest.raises(ValueError, match='accepted owner prompt contract'):
        prep.preparation_messages(accepted + '!', {})


@pytest.mark.parametrize('description,expected', [
    pytest.param('я' * 29_994 + '``````', 'я' * 29_994 + ' ', id='accepted_boundary'),
    pytest.param('до```x```после', 'до после', id='keep_prose_separate'),
])
def test_short_code_fence_cannot_expand_or_merge_the_accepted_owner_description(
    description, expected,
):
    body = json.loads(prep.preparation_messages(description, {})[1]['content'])
    assert body['description'] == expected


def test_code_exclusion_does_not_admit_an_oversized_internal_owner_description():
    with pytest.raises(ValueError, match='accepted owner prompt contract'):
        prep.preparation_messages('```tsx\n' + 'x' * 30_000 + '\n```', {})


@pytest.mark.parametrize("legacy", [
    "На первом экране только тарифы. До выбора тарифа скрыты анкета и история.",
    "Главное действие — купить подписку. Без активной подписки только тарифы; "
    "анкета, рацион, дневник и доставка закрыты. До подписки навигации нет. "
    "Если тарифов нет, кнопка покупки недоступна.",
])
async def test_old_ready_paywall_first_brief_keeps_cache_and_receives_post_data_access_guard(
    monkeypatch, legacy,
):
    from yleum_api.core import config

    fingerprint = "a" * 64
    cache = {"version": 1, "state": "ready", "input_sha256": fingerprint, "result": legacy}
    cached = prep._cached(cache, fingerprint)
    assert cached == legacy
    claim = AsyncMock(return_value=(None, cached))
    complete = AsyncMock(side_effect=AssertionError("ready cache must not dispatch a model"))
    factory = Mock(side_effect=AssertionError("ready cache must not write a new claim"))
    write = AsyncMock(side_effect=AssertionError("ready cache must not be rewritten"))
    progress = AsyncMock()
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        prompt_preparation_enabled=True,
    ))
    monkeypatch.setattr(prep, "_claim", claim)
    monkeypatch.setattr(prep, "_write", write)
    returned = await prep.prepare_prompt_brief(
        factory, SimpleNamespace(), complete=complete, emit_progress=progress,
    )
    assert returned == legacy and cache["result"] == legacy
    claim.assert_awaited_once()
    factory.assert_not_called()
    complete.assert_not_awaited()
    write.assert_not_awaited()
    progress.assert_not_awaited()
    rendered = prep.render_prepared_brief(returned)
    start = rendered.index("<prepared_implementation_plan>\n")
    end = rendered.index("\n</prepared_implementation_plan>")
    data = json.loads(rendered[start + len("<prepared_implementation_plan>\n"):end])
    assert data == {"prepared_implementation_plan": legacy}
    suffix = rendered[end + len("\n</prepared_implementation_plan>"):]
    assert "добавленные автором условия" in suffix
    assert "не расширяют ограничения исходного запроса" in suffix
    for required in (
        'Главное действие «Купить подписку» и модель по подписке сами по себе не запрещают',
        'исходного контекста',
        'явного условия владельца или подтверждённого серверного контракта',
        'визуальный приоритет покупки',
        'без превращения её в единственный разрешённый шаг из-за пустого списка тарифов',
    ):
        assert required in suffix
    assert "ненастроенные тарифы или недоступный провайдер не требуют скрывать весь" in suffix
    assert "Явное ограничение владельца на доступ ко всему продукту сохраняется" in suffix
    assert (
        "Платные данные и операции доступны только после серверного подтверждения права" in suffix
    )
    assert "заказ, возврат из оплаты или состояние клиента" in suffix
    assert prep.render_prepared_brief(legacy) == rendered
