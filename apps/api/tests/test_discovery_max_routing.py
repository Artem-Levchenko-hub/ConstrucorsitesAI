"""Explicit MAX intent survives discovery without changing generic product intent."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yleum_api.services import discovery
from yleum_api.services.generation.onboarding import _compose_build_prompt


@pytest.mark.parametrize('prompt', [
    'Создай готовое мини-приложение именно для мессенджера MAX.',
    'Мини приложение специально для MAX',
    'Приложение исключительно внутри мессенджера «Макс»',
    'Сервис только для мессенджера MAX',
    'Бот под MAX',
    'Приложение на платформе MAX',
    'Мини-приложение MAX',
    'МАКС мини-приложение',
    'MAX Mini App',
    'mini-app specifically for the MAX messenger',
    'mini app inside messenger MAX',
    'мини‑приложение именно для мессенджера “MAX”',
    'мини-приложение\nименно для\nмессенджера MAX',
])
def test_explicit_max_platform_phrasings(prompt):
    assert discovery._infer_max_miniapp_from_text(prompt)
    assert discovery._resolve_messenger_stack('realtime', prompt) == 'max_miniapp'


@pytest.mark.parametrize('prompt', [
    'Каталог на обычном сайте с max-width: 400px',
    'Шрифт Max Pro для интерфейса приложения',
    'Приложение для максимальной производительности',
    'Сравнение Telegram Mini App и MAX на обычном сайте',
    'Командный мессенджер с каналами и живым чатом',
    'mini app with a max-width layout',
])
def test_platform_match_requires_explicit_product_destination_not_a_bare_name(prompt):
    assert not discovery._infer_max_miniapp_from_text(prompt)


def test_single_request_fallback_is_exact_without_product_type_or_role_wrapper():
    for prompt in (
        'Создай готовое мини-приложение именно для мессенджера MAX.',
        'Сайт-портфолио с примерами работ',
        'CLI на Python без веб-интерфейса',
    ):
        assert discovery._fallback_brief([], prompt) == prompt
        assert discovery._fallback_brief([{'role': 'user', 'content': prompt}], '') == prompt


def test_dialog_fallback_retains_all_turns_order_and_constraints_without_site_instruction():
    result = discovery._fallback_brief([
        {'role': 'user', 'content': 'Нужен CLI, не сайт.'},
        {'role': 'assistant', 'content': 'Какие данные сохранять?'},
        {'role': 'user', 'content': 'Только локальные файлы.'},
        {'role': 'assistant', 'content': '   '},
    ], 'Без сети и регистрации.')
    assert result == (
        'Пользователь: Нужен CLI, не сайт.\n'
        'Ассистент: Какие данные сохранять?\n'
        'Пользователь: Только локальные файлы.\n'
        'Пользователь: Без сети и регистрации.'
    )
    assert discovery._fallback_brief([], '').strip()


def test_rich_max_request_takes_zero_question_path_without_a_gateway(monkeypatch):
    gateway = AsyncMock(side_effect=AssertionError('zero-question is model-free'))
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', gateway)
    prompt = (
        'Создай готовое мини-приложение именно для мессенджера MAX. '
        'Тёмная минималистичная тема, фиолетовый акцент, каталог и профиль.'
    )
    result = discovery.zero_question_build([], prompt)
    assert result is not None and result.stack == 'max_miniapp'
    assert result.brief == prompt
    gateway.assert_not_called()


class _Client:
    def __init__(self, post):
        self.post = post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.parametrize('gateway_status', [200, 503])
async def test_forced_max_build_overrides_wrong_realtime_hint_and_preserves_raw_fallback(
    monkeypatch, gateway_status,
):
    prompt = 'Создай готовое мини-приложение именно для мессенджера MAX.'
    response = SimpleNamespace(status_code=gateway_status, json=lambda: {
        'choices': [{'message': {'content': json.dumps({
            'action': 'build', 'brief': prompt, 'stack': 'realtime',
        })}}],
    })
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', lambda **_: _Client(post))
    result = await discovery.run_discovery([], prompt, asked_count=0, force_build=True)
    assert result.action == discovery.BUILD and result.stack == 'max_miniapp'
    assert result.brief == prompt
    compiled = _compose_build_prompt(result)
    assert compiled.startswith(prompt + '\n\n')
    assert 'Рекомендованный стек: max_miniapp' in compiled
    post.assert_awaited_once()


@pytest.mark.parametrize(('prompt', 'expected'), [
    ('Командный мессенджер с каналами и живым чатом', 'realtime'),
    ('Приложение с регистрацией, аккаунтами и сохранением заказов', 'nextjs_entities'),
    ('Статичный HTML без JavaScript', 'static'),
    ('Напиши Python-скрипт для CSV', 'code'),
    ('Статичный HTML без JavaScript с описанием MAX Mini App', 'static'),
    ('Напиши Python-скрипт для документации MAX Mini App', 'code'),
])
async def test_non_max_build_keeps_its_own_product_type(monkeypatch, prompt, expected):
    response = SimpleNamespace(status_code=200, json=lambda: {
        'choices': [{'message': {'content': json.dumps({
            'action': 'build', 'brief': prompt, 'stack': 'static',
        })}}],
    })
    monkeypatch.setattr(
        discovery.httpx, 'AsyncClient', lambda **_: _Client(AsyncMock(return_value=response)),
    )
    result = await discovery.run_discovery([], prompt, asked_count=0, force_build=True)
    assert result.action == discovery.BUILD and result.stack == expected
