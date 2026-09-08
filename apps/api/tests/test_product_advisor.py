from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.services import llm_client
from omnia_api.services.product_advisor import (
    AdviceContext,
    SnapshotInput,
    build_advice_context,
    choose_analysis_snapshot,
    extract_feature_inventory,
    generate_product_advice,
    is_material_change,
)


def test_material_change_distinguishes_product_work_from_cosmetics() -> None:
    cosmetic = (
        "Сделай кнопку синей и увеличь отступ",
        "Замени текст заголовка и иконку",
        "Уменьши шрифт, поправь цвет фона",
        "Поменяй цвет кнопки оплаты",
        "Сделай кнопку оплаты синей",
        "Сделай кнопку оплаты красной",
        "Сделай кнопку оплаты ярче",
        "Исправь текст на экране профиля",
    )
    material = (
        "Добавь избранное с сохранением и отдельным экраном",
        "Сделай историю заказов и повтор заказа",
        "Подключи уведомления о записи",
        "Добавь оплату и поменяй цвет кнопки",
        "Сделай поиск и обнови иконки",
        "Восстановление версии",
    )

    assert all(not is_material_change(prompt) for prompt in cosmetic)
    assert all(is_material_change(prompt) for prompt in material)


def test_choose_analysis_snapshot_reuses_latest_material_parent() -> None:
    snapshots = (
        SnapshotInput("cosmetic", "c" * 40, "Поменяй цвет заголовка"),
        SnapshotInput("feature", "b" * 40, "Добавь историю заказов"),
        SnapshotInput("initial", "a" * 40, "Приложение кофейни"),
    )

    assert choose_analysis_snapshot(snapshots).id == "feature"


def test_choose_analysis_snapshot_uses_first_generated_build_as_baseline() -> None:
    snapshots = (
        SnapshotInput("cosmetic", "c" * 40, "Поменяй цвет заголовка"),
        SnapshotInput("initial", "b" * 40, "Приложение кофейни"),
        SnapshotInput("starter", "a" * 40, None),
    )

    assert choose_analysis_snapshot(snapshots).id == "initial"


def test_choose_analysis_snapshot_falls_back_to_newest_snapshot() -> None:
    snapshots = (
        SnapshotInput("managed", "c" * 40, None),
        SnapshotInput("starter", "a" * 40, None),
    )

    assert choose_analysis_snapshot(snapshots).id == "managed"


def test_inventory_filters_secrets_dependencies_and_detects_real_features() -> None:
    inventory = extract_feature_inventory(
        {
            "src/app/page.tsx": (
                "function Search(){ return <input placeholder='Поиск'/> }\n"
                "function Favorites(){ return <button>В избранное</button> }\n"
                "if (error) return <p>Не удалось загрузить</p>"
            ),
            ".env": "PAYMENT_TOKEN=secret",
            "src/app/private.secret.ts": "super-secret-value",
            "node_modules/pkg/index.js": "analytics notifications payments",
            "pnpm-lock.yaml": "search favorites analytics",
        }
    )

    assert inventory == ("error_states", "favorites", "search")
    assert "secret" not in repr(inventory).lower()
    assert "payment" not in inventory


def test_context_uses_shared_archetype_and_discards_raw_source() -> None:
    files = {
        "src/app/page.tsx": (
            "export default function Shop(){ return <button>Добавить в корзину</button> }"
        )
    }

    context = build_advice_context(
        project_name="Кофе рядом",
        material_prompt="Сделай магазин кофе с каталогом и заказами",
        discovery_spec={"audience": "постоянные гости"},
        files=files,
    )

    assert context.archetype == "commerce"
    assert "payments" not in context.inventory
    assert "export default function" not in repr(context)


def test_context_redacts_labelled_punctuation_credentials() -> None:
    credential = "abcdefghijklmnop.qrstuvwxyz123456"

    context = build_advice_context(
        project_name="Кофе рядом",
        material_prompt=f"Добавь оплату, ключ: {credential}",
        discovery_spec=None,
        files={},
    )

    assert credential not in context.material_prompt
    assert "credential redacted" in context.material_prompt.casefold()


@pytest.mark.asyncio
async def test_model_generates_specific_advice_and_implementation_prompt() -> None:
    context = AdviceContext(
        project_name="Кофе рядом",
        material_prompt="Магазин кофе с каталогом и заказами",
        archetype="commerce",
        inventory=(),
    )
    captured: dict[str, object] = {}

    async def complete(messages, model, **kwargs):
        captured["messages"] = messages
        captured["model"] = model
        captured.update(kwargs)
        return (
            '{"items":['
            '{"id":"coffee-grind-choice","kind":"feature","title":"Выбор помола",'
            '"benefit":"Возвращайтесь к выбору быстрее",'
            '"prompt":"Добавь выбор помола кофе на карточке товара и сохраняй его в заказе."}'
            "]}"
        )

    result = await generate_product_advice(
        context,
        complete=complete,
        model="cheap-test-model",
    )

    assert result.source == "model"
    assert result.items[0].id == "coffee-grind-choice"
    assert result.items[0].title == "Выбор помола"
    assert result.items[0].benefit == "Возвращайтесь к выбору быстрее"
    assert (
        result.items[0].prompt
        == "Добавь выбор помола кофе на карточке товара и сохраняй его в заказе."
    )
    assert len(result.items) == 1
    assert captured["model"] == "cheap-test-model"
    assert captured["stage"] == "product_advisor"
    assert captured["free"] is True
    assert captured["max_tokens"] <= 2200
    assert captured["temperature"] == 0.1
    assert captured["timeout_seconds"] == 45.0


def test_context_keeps_bounded_ui_and_product_config_without_private_records() -> None:
    context = build_advice_context(
        project_name="Кофе рядом",
        material_prompt="Сделай каталог",
        discovery_spec={"audience": "любители кофе", "password": "private-short"},
        files={
            "src/app/page.tsx": "<h1>Подбор кофе по помолу</h1><button>Заказать</button>"
            '<p>client@example.com</p><script>const secret = "hidden-value"</script>',
            "data/customers.json": '{"name":"Private Customer"}',
            ".env": "PAYMENT_TOKEN=private-env",
        },
        initial_brief="Кофейня с зерном под домашние кофемашины",
        recent_changes=("Добавили выбор обжарки",),
        app_config={
            "app_type": "catalog",
            "summary": "Зерно для дома",
            "features": ["Выбор обжарки"],
            "content": [{"title": "Private Customer"}],
            "support": {"email": "support@example.com"},
            "password": "private-short",
        },
    )
    rendered = repr(context)
    assert "Подбор кофе по помолу" in rendered
    assert "Зерно для дома" in rendered
    assert "Добавили выбор обжарки" in rendered
    assert "домашние кофемашины" in rendered
    for private in (
        "private-short",
        "Private Customer",
        "client@example.com",
        "hidden-value",
        "private-env",
        "support@example.com",
    ):
        assert private not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "{}",
        '{"items":"wrong"}',
        '{"items":[null]}',
        '{"items":[{"id":"missing-fields","title":"Нет"}]}',
        json.dumps(
            {
                "items": [
                    {
                        "id": "unsafe",
                        "kind": "feature",
                        "title": "<script>bad</script>",
                        "benefit": "Польза",
                        "prompt": "Добавь выбор",
                    }
                ]
            }
        ),
    ],
)
async def test_malformed_output_is_retryable_without_generic_advice(raw: str) -> None:
    context = AdviceContext("Кофе", "Каталог кофе", "commerce", ())

    async def complete(*_args, **_kwargs):
        return raw

    with pytest.raises(ApiError) as error:
        await generate_product_advice(context, complete=complete)
    assert error.value.code == "advice_unavailable"
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_provider_failure_is_retryable_without_generic_advice() -> None:
    async def complete(*_args, **_kwargs):
        raise TimeoutError("provider unavailable")

    with pytest.raises(ApiError) as error:
        await generate_product_advice(
            AdviceContext("Кофе", "Каталог", "commerce", ()),
            complete=complete,
        )
    assert error.value.code == "advice_unavailable"


@pytest.mark.asyncio
async def test_valid_empty_advice_stays_empty() -> None:
    async def complete(*_args, **_kwargs):
        return '{"items":[]}'

    result = await generate_product_advice(
        AdviceContext("Кофе", "Каталог", "commerce", ()),
        complete=complete,
    )
    assert result.source == "model"
    assert result.items == ()


@pytest.mark.asyncio
async def test_present_feature_and_unsupported_max_capability_are_not_recommended() -> None:
    async def complete(*_args, **_kwargs):
        return json.dumps(
            {
                "items": [
                    {
                        "id": "search",
                        "kind": "feature",
                        "title": "Поиск",
                        "benefit": "Найти кофе",
                        "prompt": "Добавь поиск кофе в каталоге",
                    },
                    {
                        "id": "contacts",
                        "kind": "feature",
                        "title": "Контакты MAX",
                        "benefit": "Приглашать друзей",
                        "prompt": "Получи все контакты пользователя MAX без разрешения",
                    },
                ]
            }
        )

    result = await generate_product_advice(
        AdviceContext("Кофе", "Каталог", "commerce", ("search",)),
        complete=complete,
    )
    assert result.items == ()


@pytest.mark.asyncio
async def test_complete_chat_free_override_reaches_gateway_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="http://gateway"),
    )

    def fake_client(**kwargs):
        captured["timeout"] = kwargs["timeout"]
        return FakeClient()

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", fake_client)

    result = await llm_client.complete_chat(
        [{"role": "user", "content": "rank"}],
        "cheap-model",
        free=True,
        timeout_seconds=12.0,
    )

    assert result == "ok"
    assert captured["json"]["metadata"]["free"] is True
    assert captured["timeout"].read == 12.0
