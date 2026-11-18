"""Preparation request preserves client choices and truthful MAX/data boundaries."""
import json

import pytest

from yleum_api.services import llm_client
from yleum_api.services import prompt_preparation as prep


@pytest.mark.parametrize('description', [
    'Каталог пуст. На первом экране нужна декоративная иллюстрация, без выдуманных товаров.',
    'Без фотографий и декоративной графики. Имя пользователь вводит в форме.',
    'Нужен аватар в профиле, если пользователь разрешённым способом добавил его сам.',
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
