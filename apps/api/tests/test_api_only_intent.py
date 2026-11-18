"""Owner scope is a request, not a negative phrase, example or product name."""
import pytest

from yleum_api.services.max_generation_contract import is_api_only_request


@pytest.mark.parametrize('idea,expected', [
    ('API-only: provide an authenticated read-only endpoint.', True),
    ('Только API каталога и заказов, без интерфейса.', True),
    ('Нужен «API-only»', True),
    ('Сделай API-only: список растений.', True),
    ('I need API-only: no UI.', True),
    ('Build `API-only` service.', True),
    ('Build an API-only service.', True),
    ('I need an `API-only` backend.', True),
    ('Нужен ‘API-only’', True),
    ('API-only не нужен: нужен интерфейс', False),
    ('API-only — не нужно. Нужен интерфейс.', False),
    ('Не API-only, нужен интерфейс', False),
    ('Не только API: нужен интерфейс', False),
    ('Not only API, we need an interface.', False),
    ('API-only is not needed: build an interface.', False),
    ('Назови кнопку «API-only», нужен полноценный интерфейс.', False),
    ('Покажи текст "только API" в справке.', False),
    ('Пример, так не надо:\n```text\nAPI-only\n```\nНужен интерфейс.', False),
    ('Example, do not implement:\n~~~text\nAPI-only\n~~~\nNeed a user interface.', False),
    ('Название продукта: API-only', False),
    ('Сделай каталог с интерфейсом и своим API.', False),
])
@pytest.mark.parametrize('wizard', [False, True])
def test_only_an_affirmative_owner_request_selects_the_api_only_branch(idea, expected, wizard):
    prompt = idea
    if wizard:
        prompt = (
            'Создай готовое мини-приложение именно для мессенджера MAX.\n'
            'Название продукта: API-only.\n'
            f'Что должно делать приложение: {idea}.\n'
            'Целевая аудитория: пользователи.\n'
            'Управление в Yleum: Каталог, Заказы. Используй .omnia/management.json.\n'
        )
    assert is_api_only_request(prompt) is expected
