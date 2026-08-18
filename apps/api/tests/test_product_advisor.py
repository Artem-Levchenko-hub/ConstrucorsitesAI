from __future__ import annotations

from omnia_api.services.product_advisor import (
    AdviceContext,
    SnapshotInput,
    build_advice_context,
    candidate_advice,
    choose_analysis_snapshot,
    extract_feature_inventory,
    is_material_change,
)


def test_material_change_distinguishes_product_work_from_cosmetics() -> None:
    cosmetic = (
        "Сделай кнопку синей и увеличь отступ",
        "Замени текст заголовка и иконку",
        "Уменьши шрифт, поправь цвет фона",
    )
    material = (
        "Добавь избранное с сохранением и отдельным экраном",
        "Сделай историю заказов и повтор заказа",
        "Подключи уведомления о записи",
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


def test_candidate_advice_suppresses_present_features_and_returns_three() -> None:
    context = AdviceContext(
        project_name="Кофе рядом",
        material_prompt="Магазин кофе с каталогом и заказами",
        archetype="commerce",
        inventory=("favorites", "search"),
    )

    items = candidate_advice(context)

    assert len(items) == 3
    assert {item.id for item in items}.isdisjoint({"smart-search", "saved-favorites"})
    assert any(item.kind == "improvement" for item in items)


def test_candidate_prompts_are_actionable_vertical_slices() -> None:
    context = AdviceContext(
        project_name="Тренировки",
        material_prompt="Дневник тренировок и планы",
        archetype="fitness-health",
        inventory=(),
    )

    items = candidate_advice(context)

    assert len(items) == 3
    for item in items:
        prompt = item.prompt.casefold()
        assert "сохран" in prompt
        assert "loading" in prompt
        assert "empty" in prompt
        assert "error" in prompt
        assert "success" in prompt
        assert "сохрани текущ" in prompt
