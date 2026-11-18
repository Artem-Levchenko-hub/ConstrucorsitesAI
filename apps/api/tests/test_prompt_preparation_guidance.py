"""Preparation request preserves client choices and truthful MAX/data boundaries."""
import json

import pytest

from yleum_api.services import llm_client
from yleum_api.services import prompt_preparation as prep


@pytest.mark.parametrize('description', [
    'Каталог пуст. На первом экране нужна декоративная иллюстрация, без выдуманных товаров.',
    'Без фотографий и декоративной графики. Имя пользователь вводит в форме.',
    'Нужен аватар в профиле, если пользователь разрешённым способом добавил его сам.',
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
