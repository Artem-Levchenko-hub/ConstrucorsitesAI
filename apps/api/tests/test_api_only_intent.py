"""Owner scope is a request, not a negative phrase, example or product name."""
import pytest

from yleum_api.services.max_generation_contract import (
    is_api_only_request,
    positive_request_scope,
    requested_max_capabilities,
)


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
    ('Sort only API results, catalog UI unchanged.', True),
    ('Sort `only API` results, catalog UI unchanged.', True),
    ("Don't sort only API results; build the interface.", False),
    ('Sort not only API results; include the interface.', False),
    ('Sort buttons named "only API" in the interface.', False),
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
@pytest.mark.parametrize('wizard', ['none', 'legacy', 'compact'])
def test_only_an_affirmative_owner_request_selects_the_api_only_branch(idea, expected, wizard):
    prompt = idea
    if wizard == 'legacy':
        prompt = (
            'Создай готовое мини-приложение именно для мессенджера MAX.\n'
            'Название продукта: API-only.\n'
            f'Что должно делать приложение: {idea}.\n'
            'Целевая аудитория: пользователи.\n'
            'Управление в Yleum: Каталог, Заказы. Используй .omnia/management.json.\n'
        )
    elif wizard == 'compact':
        prompt = (
            f'Задание MAX\n{idea}\n\nНазвание продукта: API-only.\n'
            'Сценарий: Своя идея.\nУправление в Yleum: Каталог, Заказы.'
        )
    assert is_api_only_request(prompt) is expected


def test_compact_wizard_preserves_owner_actions_without_inventing_visitor_features():
    prompt = (
        'Задание MAX\nУчу слова.\n\nНазвание продукта: первая идея\nсохрани этот текст.\n'
        '\nНазвание продукта: Заказы.\nСценарий: Своя идея.\n'
        'Главное действие пользователя: Запомнить слово.\n'
        'Нужные возможности: Карточки\n  и озвучка.\n'
        'Управление в Yleum: Каталог, Биллинг.\nЯвно исключено владельцем: Корзина.'
    )
    scope = positive_request_scope(prompt)
    for required in (
        'Учу слова', 'первая идея', 'сохрани этот текст', 'Запомнить слово', 'Карточки', 'озвучка',
    ):
        assert required in scope
    for configuration in ('Заказы', 'Каталог', 'Биллинг', 'Корзина'):
        assert configuration not in scope


def test_compact_wizard_explicit_product_scenarios_remain_in_scope():
    base = 'Задание MAX\nСделай приложение для сети кофеен.\n\nНазвание продукта: Кофейня.\n'
    selected = requested_max_capabilities(base + 'Сценарии: Лояльность, Каталог.')
    assert {'catalog', 'loyalty'} <= {key for key, _, _ in selected}
