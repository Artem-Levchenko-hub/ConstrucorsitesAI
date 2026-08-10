from __future__ import annotations

import json

import pytest

from omnia_api.services.max_generation_contract import (
    MAX_REQUIRED_POST_SEE_SKILL,
    MAX_REQUIRED_PREWRITE_SKILLS,
    build_max_product_contract,
    max_completion_gap,
    max_demo_data_rejection,
    max_source_completion_gap,
    normalize_max_globals_css,
    requested_max_capabilities,
)

COMPLEX_BRIEF = """
ИИ тренер следит за сном и питанием, показывает статистику тренировок с графиком.
Нужны профиль пользователя, история действий, уведомления, loading/empty/error/retry.
"""


def _design_spec() -> str:
    return json.dumps(
        {
            "product_promise": "Понятная картина восстановления спортсмена",
            "primary_action": "Разобрать тренировку",
            "directions_considered": [
                {"name": "Data instrument", "idea": "Плотный спортивный прибор"},
                {"name": "Coach dialogue", "idea": "Эмоциональный тренер"},
                {"name": "Training editorial", "idea": "Редакционный дневник"},
            ],
            "chosen_direction": "Data instrument",
            "chosen_rationale": "Лучше раскрывает статистику и главное действие",
            "screens": ["Обзор", "История", "ИИ-тренер", "Профиль"],
            "visual_system": {"type": "контрастная", "density": "athletic compact"},
            "motion": ["press feedback", "progress morph"],
            "states": ["loading", "empty", "error", "success"],
        },
        ensure_ascii=False,
    )


def _complete_files() -> dict[str, str]:
    return {
        ".omnia/max-design-spec.json": _design_spec(),
        "src/app/page.tsx": (
            'import { useEffect } from "react";\n'
            'import { useMaxApp } from "@/components/MaxAppProvider";\n'
            "import { createMaxAction, getMaxActions, requestOmniaAI } "
            'from "@/lib/omnia/integration-client";\n'
            "async function persist(){ const { answer } = await requestOmniaAI({ "
            "message: 'Разбери', instructions: 'ИИ тренер', context: {} }); "
            "await createMaxAction('open', { answer }); "
            "await getMaxActions(); } "
            "export default function Page(){ const { user } = useMaxApp(); "
            "useEffect(() => { async function restore(){ await getMaxActions(); } "
            "void restore(); }, []); "
            "return <main><button onClick={persist}>Сохранить</button>ИИ тренер: тренировки, сон, "
            "питание, статистика, график, профиль, история, уведомления, loading, empty, "
            "error, retry, {user?.firstName}</main>; }"
        ),
        "src/components/Coach.tsx": "export function Coach(){ return <button>Анализ</button>; }\n"
        + "// coach interaction state\n" * 10,
        "src/components/HealthLog.tsx": (
            "export function HealthLog(){ return <section>Сон и питание</section>; }\n"
        )
        + "// health log interaction state\n" * 10,
        "src/components/Stats.tsx": (
            "export function Stats(){ return <section>График тренировок</section>; }\n"
        )
        + "// statistics chart interaction\n" * 10,
        "src/components/Profile.tsx": (
            "export function Profile(){ return <section>Профиль и история</section>; }\n"
        )
        + "// profile history interaction\n" * 10,
    }


def _complete_evidence() -> dict[str, int]:
    return {
        **{f"skill:{skill}": 1 for skill in MAX_REQUIRED_PREWRITE_SKILLS},
        f"skill:{MAX_REQUIRED_POST_SEE_SKILL}": 1,
        "visual_evaluation_after_see": 1,
        "build_after_write": 1,
        "runtime_check_after_write": 1,
        "see_after_write": 1,
    }


def _complete_single_file() -> dict[str, str]:
    """A real multi-view app may be intentionally cohesive in one component."""

    page = """"use client";
import { useEffect, useState } from "react";
import { useMaxApp } from "@/components/MaxAppProvider";
import { createMaxAction, getMaxActions, requestOmniaAI } from "@/lib/omnia/integration-client";

export default function Page() {
  const { mode, user, error } = useMaxApp();
  const [view, setView] = useState("dashboard");
  const [loading, setLoading] = useState(false);
  const [empty, setEmpty] = useState(false);
  const [failure, setFailure] = useState(error || "");
  useEffect(() => {
    async function restore() { await getMaxActions(); }
    void restore();
  }, []);
  async function analyze() {
    setLoading(true);
    try {
      const { answer } = await requestOmniaAI({
        message: "Разбери тренировку",
        instructions: "ИИ тренер",
        context: { view },
      });
      await createMaxAction("workout_analysis", { answer });
      await getMaxActions();
      setEmpty(false);
    } catch { setFailure("Ошибка анализа — повторите"); } finally { setLoading(false); }
  }
  return <main>
    <header><h1>Фитнес для {user?.firstName || "спортсмена"}</h1></header>
    <nav aria-label="Разделы">
      {["Тренировки", "Сон", "Питание", "Статистика", "Профиль",
        "История", "Уведомления"].map(label =>
        <button key={label} onClick={() => setView(label)}>{label}</button>)}
    </nav>
    <section><h2>{view}</h2><p>График динамики и восстановление после тренировок.</p></section>
    <button onClick={analyze} disabled={loading}>
      {loading ? "Загрузка" : "Разобрать тренировку"}
    </button>
    {empty && <p>Пусто: добавьте первую тренировку</p>}
    {failure && <button onClick={analyze}>Повтор</button>}
    {mode === "error" && <p>Ошибка MAX</p>}
  </main>;
}
"""
    return {
        ".omnia/max-design-spec.json": _design_spec(),
        "src/app/page.tsx": page,
    }


def test_contract_extracts_explicit_brief_and_forbids_fake_ai() -> None:
    labels = [label for _key, label, _needles in requested_max_capabilities(COMPLEX_BRIEF)]
    contract = build_max_product_contract(COMPLEX_BRIEF)

    assert "сон и восстановление" in labels
    assert "питание" in labels
    assert "уведомления" in labels
    assert "requestOmniaAI" in contract
    assert "never dump a long unbroken AI paragraph" in contract
    assert "fake timers" in contract
    assert "loading, empty, error/retry" in contract
    assert ".omnia/max-design-spec.json" in contract
    assert "three distinct directions_considered" in contract
    assert "validated MAX initData" in contract
    assert "hardcoded demo" in contract
    assert "static business menus" in contract.lower()
    assert "primary scenario works on first open" in contract
    assert "coherent on every screen and state" in contract
    assert "browser/default blue buttons" in contract
    assert "never nest a button/link" in contract
    assert "sibling controls" in contract
    assert "real h1/h2" in contract
    assert "stable aria-label" in contract


def test_completion_requires_persistent_project_specific_design_spec() -> None:
    files = _complete_files()
    files.pop(".omnia/max-design-spec.json")
    assert "persistent art direction" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files[".omnia/max-design-spec.json"] = "{}"
    assert "design spec is incomplete" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    spec = json.loads(_design_spec())
    spec["directions_considered"] = ["same", "same", "same"]
    files[".omnia/max-design-spec.json"] = json.dumps(spec)
    assert "three genuinely distinct" in str(max_source_completion_gap(COMPLEX_BRIEF, files))


def test_named_yookassa_and_iiko_require_real_managed_calls() -> None:
    brief = "Ресторан: каталог iiko, заказы и реальная оплата через ЮKassa."
    files = _complete_single_file()
    files["src/app/page.tsx"] += "\n// Каталог ресторана, iiko, ЮKassa, заказы\n"

    gap = max_source_completion_gap(brief, files)
    assert "getOmniaIntegrations" in str(gap)

    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { createMaxAction, getMaxActions, requestOmniaAI }",
        "import { createMaxAction, getMaxActions, requestOmniaAI, "
        "getOmniaIntegrations, createOmniaPayment, getOmniaCatalog }",
    )
    files["src/app/page.tsx"] += """
async function integrationStatus() {
  return await getOmniaIntegrations();
}
void integrationStatus;
"""
    gap = max_source_completion_gap(brief, files)
    assert "createOmniaPayment" in str(gap)

    files["src/app/page.tsx"] += """
async function startPayment() {
  return await createOmniaPayment({ amount: 100, description: "Заказ", return_url: "/" });
}
void startPayment;
"""
    gap = max_source_completion_gap(brief, files)
    assert "confirmation_url" in str(gap)

    files["src/app/page.tsx"] += """
async function confirmPayment() {
  const payment = await startPayment();
  if (payment.confirmation_url) window.location.assign(payment.confirmation_url);
}
void confirmPayment;
"""
    gap = max_source_completion_gap(brief, files)
    assert "getOmniaCatalog" in str(gap)

    files["src/app/page.tsx"] += """
async function loadRestaurantCatalog() {
  return await getOmniaCatalog();
}
void loadRestaurantCatalog;
"""
    assert max_source_completion_gap(brief, files) is None


def test_incremental_edit_can_explicitly_remove_provider_requirement() -> None:
    brief = (
        "ИСХОДНЫЙ БРИФ ПРОДУКТА:\nЗаказы с оплатой через ЮKassa.\n\n"
        "ТЕКУЩАЯ ПРАВКА:\nУбери интеграцию с ЮKassa."
    )

    assert max_source_completion_gap(brief, _complete_single_file()) is None


def test_completion_rejects_untouched_canvas_and_thin_cosmetic_page() -> None:
    assert "no product entry" in str(max_completion_gap(COMPLEX_BRIEF, {}, {})).lower()

    canvas = {"src/app/page.tsx": '<main data-testid="max-generation-canvas">Фитнес</main>'}
    assert "retired generation canvas" in str(max_completion_gap(COMPLEX_BRIEF, canvas, {}))

    thin = {
        ".omnia/max-design-spec.json": _design_spec(),
        "src/app/page.tsx": (
            "export default function Page(){return <main>ИИ тренер, тренировки, "
            "профиль, история</main>}"
        ),
    }
    gap = max_completion_gap(COMPLEX_BRIEF, thin, {})
    assert gap is not None
    assert "missing" in gap.lower() or "thin" in gap.lower()


def test_css_imports_are_moved_before_tailwind_and_product_rules() -> None:
    broken = """@import "tailwindcss";

:root { color: black; }
@import url('https://fonts.example/family?a=1;2');
.card { display: grid; }
"""

    fixed = normalize_max_globals_css(broken)

    assert fixed.startswith(
        "@import url('https://fonts.example/family?a=1;2');\n@import \"tailwindcss\";"
    )
    assert fixed.index('@import "tailwindcss";') < fixed.index(":root")
    assert fixed.endswith("\n")


def test_safe_css_import_order_is_byte_stable() -> None:
    css = """@import url('https://fonts.example/family');
@import "tailwindcss";

:root { color: black; }
"""

    assert normalize_max_globals_css(css) == css


def test_css_import_normalizer_deduplicates_repair_artifacts() -> None:
    font = "@import url('https://fonts.example/family');"
    css = f'{font}\n@import "tailwindcss";\n{font}\n\n:root {{ color: black; }}\n'

    fixed = normalize_max_globals_css(css)

    assert fixed.count(font) == 1
    assert fixed.startswith(f'{font}\n@import "tailwindcss";')


def test_completion_rejects_fake_ai_even_when_feature_words_exist() -> None:
    files = _complete_files()
    files["src/app/page.tsx"] = (
        files["src/app/page.tsx"]
        .replace(", requestOmniaAI", "")
        .replace(" void requestOmniaAI;", "")
    )

    gap = max_completion_gap(
        COMPLEX_BRIEF,
        files,
        {"runtime_check_after_write": 1, "see_after_write": 1, "see": 2, "probe": 1},
    )

    assert gap is not None
    assert "requestOmniaAI" in gap


def test_completion_rejects_hardcoded_demo_user_records() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const demoWorkouts = [
  { id: "demo-1", title: "Утренняя тренировка", duration: 45 },
  { id: "demo-2", title: "Интервальный бег", duration: 30 },
];
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert gap is not None
    assert "demo user data" in gap.lower()


def test_completion_rejects_unlabelled_seeded_user_records_and_profiles() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const workouts = [{ id: "1", title: "Утренняя тренировка" }];
"""
    assert "workouts" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const profile = { firstName: "Тест", username: "fitness_pro" };
"""
    assert "profile" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const entries = [{ id: "1", userId: "42", title: "Готовая запись" }];
"""
    assert "entries" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const initialState = [{ id: "1", title: "Кардио", duration: 45 }];
"""
    assert "initialState" in str(max_source_completion_gap(COMPLEX_BRIEF, files))


def test_instructional_copy_is_not_mistaken_for_seeded_records() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] += (
        'const onboardingCopy = "Sample workouts are examples, not saved history";'
    )

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_completion_rejects_generic_identity_fallback_but_allows_neutral_copy() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const displayName = user?.first_name ?? "Пользователь";
"""
    assert "generic identity fallback" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const displayName = user?.first_name || "Гость MAX";
"""
    assert "generic identity fallback" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const greeting = user?.first_name ? `Рады видеть, ${user.first_name}` : "Начнём с цели";
const aiRole = "User";
// "Пользователь" is forbidden as a generic fallback.
"""
    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_empty_profile_draft_is_not_mistaken_for_seeded_profile() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] += """
const profileDraft = { firstName: "", email: "" };
"""
    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_demo_data_guard_only_applies_to_model_owned_product_source() -> None:
    content = 'const sampleOrders = [{ id: "sample-1" }];'

    assert "truthful empty state" in str(max_demo_data_rejection("src/app/page.tsx", content))
    assert max_demo_data_rejection("src/lib/omnia/max-config.ts", content) is None


def test_static_workout_catalog_is_not_mistaken_for_user_history() -> None:
    catalog = """
const WORKOUT_CATALOG = [
  { id: "mobility-15", title: "Мобильность", duration: 15, sets: 3, reps: "8" },
];
"""
    library = """
const WORKOUT_LIBRARY = [
  { id: "strength-30", title: "Сила", duration: 30, sets: 4, reps: "10" },
];
"""

    assert max_demo_data_rejection("src/components/product/catalog.ts", catalog) is None
    assert max_demo_data_rejection("src/components/product/library.ts", library) is None

    catalog_with_later_state = (
        catalog + "\ntype ScreenState = { status: 'idle' | 'loading'; progress: number };"
    )
    assert (
        max_demo_data_rejection("src/components/product/catalog.ts", catalog_with_later_state)
        is None
    )

    catalog_with_wording = """
const WORKOUT_CATALOG = [
  {
    id: "daily-20",
    title: "status: ежедневная практика",
    description: "progress: от простого к сложному",
    duration: 20,
    /* completed: wording in a maintainer comment only */
  },
];
"""
    assert (
        max_demo_data_rejection("src/components/product/catalog.ts", catalog_with_wording) is None
    )


def test_static_business_menu_is_not_mistaken_for_user_orders() -> None:
    menu = """
const FALLBACK_MENU = [
  {
    id: "brioche",
    name: "Бриошь с корицей",
    category: "Выпечка",
    description: "Воздушное тесто и корица",
    composition: "Мука, молоко, масло, корица",
    allergens: ["глютен", "лактоза"],
    price: 320,
    modifiers: [{ id: "warm", name: "Подогреть", price: 0 }],
  },
];
"""

    assert max_demo_data_rejection("src/components/product/menu.ts", menu) is None


@pytest.mark.parametrize("name", ["supportLabels", "dict", "statusLookup", "fieldMapping"])
def test_ui_lookup_arrays_are_not_mistaken_for_user_records(name: str) -> None:
    content = f"""
const {name} = [
  {{ id: "price", label: "Цена", price: 149, email: "Электронная почта" }},
];
"""

    assert max_demo_data_rejection("src/components/product/ProductApp.tsx", content) is None


@pytest.mark.parametrize("name", ["supportLabels", "dict", "statusLookup", "fieldMapping"])
def test_ui_lookup_alias_cannot_hide_user_activity(name: str) -> None:
    content = f"""
const {name} = [
  {{ id: "done", label: "Заказ", price: 149, orderId: "order-1", completed: true }},
];
"""

    assert "demo user data" in str(
        max_demo_data_rejection("src/components/product/ProductApp.tsx", content)
    ).lower()


def test_fallback_items_are_reference_content_at_write_time() -> None:
    content = """
const FALLBACK_ITEMS = [
  { id: "bread", title: "Хлеб", price: 149 },
];
"""

    assert max_demo_data_rejection("src/components/product/ProductApp.tsx", content) is None


def test_managed_catalog_cannot_be_hidden_by_populated_fallback() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = (
        'import "../components/product/catalog";\n' + files["src/app/page.tsx"]
    )
    files["src/lib/omnia/max-config.ts"] = """
export const omniaMaxConfig = {
  content: [{ id: "bread", title: "Бородинский хлеб", price: "149 ₽", active: true }],
};
"""
    files["src/components/product/catalog.ts"] = """
const FALLBACK_MENU = [
  { id: "other", name: "Другое изделие", price: 320 },
];
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert gap is not None
    assert "canonical catalog content" in gap
    assert "FALLBACK_MENU" in gap


def test_empty_managed_catalog_can_use_compact_reference_catalog() -> None:
    files = _complete_single_file()
    files["src/lib/omnia/max-config.ts"] = "export const omniaMaxConfig = { content: [] };"
    files["src/components/product/catalog.ts"] = """
const FALLBACK_MENU = [
  { id: "brioche", name: "Бриошь", price: 320 },
];
"""

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_managed_formatted_price_must_not_use_number_directly() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = (
        'import "../components/product/catalog";\n' + files["src/app/page.tsx"]
    )
    files["src/lib/omnia/max-config.ts"] = """
export const omniaMaxConfig = {
  content: [{ id: "bread", title: "Бородинский хлеб", price: "149 ₽", active: true }],
};
"""
    files["src/components/product/catalog.ts"] = """
export function mapItem(raw: { title: string; price: string | number }) {
  const price = typeof raw.price === "string" ? Number(raw.price) : raw.price;
  return { id: raw.title, name: raw.title, price };
}
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert gap is not None
    assert "formatted strings" in gap
    assert "Number" in gap


def test_unreferenced_stale_fallback_does_not_block_repaired_product() -> None:
    files = _complete_single_file()
    files["src/lib/omnia/max-config.ts"] = """
export const omniaMaxConfig = {
  content: [{ id: "bread", title: "Бородинский хлеб", price: "149 ₽", active: true }],
};
"""
    files["src/components/product/catalog.ts"] = """
const FALLBACK_MENU = [
  { id: "stale", name: "Старый товар", price: 320 },
];
export function stale(raw: { price: string }) {
  return typeof raw.price === "string" ? Number(raw.price) : raw.price;
}
"""

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_managed_config_cannot_be_cast_to_any() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] += "\nconst support = String((omniaMaxConfig as any).support);"

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert gap is not None
    assert "casts omniaMaxConfig" in gap
    assert "support fields" in gap


@pytest.mark.parametrize(
    "user_fields",
    [
        'status: "completed", date: "2026-08-08"',
        'userId: "42", orderId: "order-1"',
        '"completed": true, "createdAt": "2026-08-08"',
    ],
)
def test_static_business_menu_cannot_hide_user_activity(user_fields: str) -> None:
    menu = f"""
const FALLBACK_MENU = [
  {{ id: "brioche", name: "Бриошь", price: 320, {user_fields} }},
];
"""

    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/menu.ts", menu)).lower()
    )


def test_fake_named_business_menu_is_still_rejected() -> None:
    menu = 'const DEMO_MENU = [{ id: "dish-1", name: "Бриошь", price: 320 }];'

    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/menu.ts", menu)).lower()
    )


def test_static_catalog_with_user_activity_fields_is_still_rejected() -> None:
    content = """
const WORKOUT_CATALOG = [
  { id: "done-1", title: "Сила", userId: "42", completedAt: "2026-08-08" },
];
"""

    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/catalog.ts", content)).lower()
    )

    later_record = """
const WORKOUT_CATALOG = [
  { id: "reference-1", title: "Сила", duration: 30 },
  { id: "done-1", title: "Кардио", userId: "42", completedAt: "2026-08-08" },
];
"""
    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/catalog.ts", later_record)).lower()
    )

    deceptive_terminator = """
const WORKOUT_CATALOG = [
  { id: "reference-1", title: "];", duration: 30 },
  { id: "done-1", title: "Кардио", userId: "42", completed: true },
];
"""
    assert (
        "demo user data"
        in str(
            max_demo_data_rejection("src/components/product/catalog.ts", deceptive_terminator)
        ).lower()
    )


@pytest.mark.parametrize(
    "activity_fields",
    [
        'completed: true, date: "2026-08-08"',
        'status: "completed", happenedAt: "2026-08-08"',
        "progress: 75, streak: 4",
        'firstName: "Анна", username: "fitness_pro"',
    ],
)
def test_static_catalog_alias_cannot_hide_fake_activity(activity_fields: str) -> None:
    content = f'const WORKOUT_LIBRARY = [{{ id: "done-1", {activity_fields} }}];'

    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/library.ts", content)).lower()
    )


def test_static_catalog_alias_cannot_hide_quoted_activity_keys() -> None:
    content = """
const WORKOUT_CATALOG = [
  { "id": "done-1", "status": "completed", "date": "2026-08-08" },
];
"""

    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/catalog.ts", content)).lower()
    )


def test_user_activity_collection_cannot_hide_behind_catalog_suffix() -> None:
    content = 'const ORDER_CATALOG = [{ id: "order-1", title: "Заказ" }];'

    assert (
        "demo user data"
        in str(max_demo_data_rejection("src/components/product/catalog.ts", content)).lower()
    )


def test_completion_requires_only_max_compatible_runtime_proof() -> None:
    files = _complete_files()
    gap = max_completion_gap(COMPLEX_BRIEF, files, {})
    assert gap is not None
    assert "capability packs" in gap

    skill_evidence = {f"skill:{skill}": 1 for skill in MAX_REQUIRED_PREWRITE_SKILLS}
    gap = max_completion_gap(COMPLEX_BRIEF, files, skill_evidence)
    assert gap is not None
    assert "runtime_check" in gap

    evidence = _complete_evidence()
    assert max_completion_gap(COMPLEX_BRIEF, files, evidence) is None


def test_source_gap_is_independent_from_broken_max_preview_tooling() -> None:
    files = _complete_files()

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None
    assert (
        max_completion_gap(
            COMPLEX_BRIEF,
            files,
            {**_complete_evidence(), "probe_failed": 3, "see_failed": 3},
        )
        is None
    )


def test_complete_single_file_product_is_not_rejected_for_source_layout() -> None:
    files = _complete_single_file()

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None
    assert (
        max_completion_gap(
            COMPLEX_BRIEF,
            files,
            _complete_evidence(),
        )
        is None
    )


def test_completion_requires_verified_identity_and_read_after_write() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "useMaxApp()", "{} as { mode: string; user: null; error: string }"
    )
    gap = max_source_completion_gap(COMPLEX_BRIEF, files)
    assert "verified MAX account" in str(gap)

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "  useEffect(() => {\n"
        "    async function restore() { await getMaxActions(); }\n"
        "    void restore();\n"
        "  }, []);\n",
        "  void getMaxActions;\n",
    )
    gap = max_source_completion_gap(COMPLEX_BRIEF, files)
    assert "does not restore it after reload" in str(gap)


def test_completion_rejects_local_identity_and_persistence_stubs() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        'import { useMaxApp } from "@/components/MaxAppProvider";',
        "function useMaxApp() { return { mode: 'ready', user: null, error: '' }; }",
    )
    assert "Import useMaxApp" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { createMaxAction, getMaxActions, requestOmniaAI } "
        'from "@/lib/omnia/integration-client";',
        'import { requestOmniaAI } from "@/lib/omnia/integration-client";\n'
        "async function createMaxAction() {}\n"
        "async function getMaxActions() { return { actions: [] }; }\n",
    )
    assert "Import createMaxAction" in str(max_source_completion_gap(COMPLEX_BRIEF, files))


def test_managed_import_decoys_cannot_authorize_local_stubs_in_another_module() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        'import { useMaxApp } from "@/components/MaxAppProvider";',
        "function useMaxApp() { return { mode: 'ready', user: null, error: '' }; }",
    )
    files["src/components/ManagedImports.tsx"] = (
        'import { useMaxApp } from "@/components/MaxAppProvider";\n'
        "export const managedIdentityReference = useMaxApp;"
    )
    assert "Import useMaxApp" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { createMaxAction, getMaxActions, requestOmniaAI } "
        'from "@/lib/omnia/integration-client";',
        'import { requestOmniaAI } from "@/lib/omnia/integration-client";\n'
        "async function createMaxAction() {}\n"
        "async function getMaxActions() { return { actions: [] }; }",
    )
    files["src/components/ManagedImports.tsx"] = (
        "import { createMaxAction, getMaxActions } "
        'from "@/lib/omnia/integration-client";\n'
        "export const managedActionReferences = [createMaxAction, getMaxActions];"
    )
    assert "Import createMaxAction" in str(max_source_completion_gap(COMPLEX_BRIEF, files))


def test_aliased_managed_imports_cannot_authorize_same_module_stubs() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        'import { useMaxApp } from "@/components/MaxAppProvider";',
        'import { useMaxApp as managedUseMaxApp } from "@/components/MaxAppProvider";\n'
        "void managedUseMaxApp;\n"
        "function useMaxApp() { return { mode: 'ready', user: null, error: '' }; }",
    )
    assert "Import useMaxApp" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { createMaxAction, getMaxActions, requestOmniaAI } "
        'from "@/lib/omnia/integration-client";',
        "import { createMaxAction as managedCreate, getMaxActions as managedGet, "
        'requestOmniaAI } from "@/lib/omnia/integration-client";\n'
        "void managedCreate; void managedGet;\n"
        "async function createMaxAction() {}\n"
        "async function getMaxActions() { return { actions: [] }; }",
    )
    assert "Import createMaxAction" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { createMaxAction, getMaxActions, requestOmniaAI } "
        'from "@/lib/omnia/integration-client";',
        "import { createMaxAction, getMaxActions, requestOmniaAI as managedAI } "
        'from "@/lib/omnia/integration-client";\n'
        "void managedAI;\n"
        "async function requestOmniaAI() { return { answer: '' }; }",
    )
    assert "requestOmniaAI" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        'import { useMaxApp } from "@/components/MaxAppProvider";',
        'import { type useMaxApp } from "@/components/MaxAppProvider";\n'
        "function useMaxApp() { return { mode: 'ready', user: null, error: '' }; }",
    )
    assert "Import useMaxApp" in str(max_source_completion_gap(COMPLEX_BRIEF, files))


def test_commented_pseudo_imports_and_calls_do_not_satisfy_managed_runtime() -> None:
    files = _complete_single_file()
    files["src/app/page.tsx"] = (
        files["src/app/page.tsx"]
        .replace(
            'import { useMaxApp } from "@/components/MaxAppProvider";',
            '// import { useMaxApp } from "@/components/MaxAppProvider";',
        )
        .replace(
            "const { mode, user, error } = useMaxApp();",
            "// const managed = useMaxApp();\n"
            "  const { mode, user, error } = { mode: 'ready', user: null, error: '' };",
        )
    )
    assert "Import useMaxApp" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { createMaxAction, getMaxActions, requestOmniaAI } "
        'from "@/lib/omnia/integration-client";',
        "// import { createMaxAction, getMaxActions, requestOmniaAI } "
        'from "@/lib/omnia/integration-client";\n'
        "async function createMaxAction() {}\n"
        "async function getMaxActions() { return { actions: [] }; }\n"
        "async function requestOmniaAI() { return { answer: '' }; }",
    )
    assert "requestOmniaAI" in str(max_source_completion_gap(COMPLEX_BRIEF, files))

    files = _complete_single_file()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        'import { useMaxApp } from "@/components/MaxAppProvider";',
        "const fakeImport = `import { useMaxApp } "
        'from "@/components/MaxAppProvider";`;\n'
        "function useMaxApp() { return { mode: 'ready', user: null, error: '' }; }",
    )
    assert "Import useMaxApp" in str(max_source_completion_gap(COMPLEX_BRIEF, files))


def test_managed_core_copy_cannot_fake_product_coverage() -> None:
    files = {
        ".omnia/max-design-spec.json": _design_spec(),
        "src/app/page.tsx": ("export default function Page(){return <main>Привет</main>}\n" * 20),
        "src/lib/omnia/max-config.ts": COMPLEX_BRIEF * 20,
        "src/lib/omnia/client.ts": (
            "requestOmniaAI createMaxAction getMaxActions loading empty error retry" * 100
        ),
        "src/components/MaxAppProvider.tsx": "loading empty error retry" * 100,
    }

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert gap is not None
    assert "missing" in gap.lower()


def test_completion_rejects_product_db_bypass_but_allows_managed_routes() -> None:
    files = _complete_files()
    files["src/app/api/workouts/route.ts"] = (
        'import { db } from "@/lib/db";\n'
        'import { workouts } from "@/lib/db/schema";\n'
        "export async function POST() { return db.insert(workouts).values({}); }"
    )
    files["src/app/api/omnia/actions/route.ts"] = (
        'import { db } from "@/lib/db";\nexport async function POST() { return db; }'
    )
    evidence = {
        "build_after_write": 1,
        "runtime_check_after_write": 1,
        "see_after_write": 1,
        "see": 2,
        "probe": 1,
    }

    gap = max_completion_gap(COMPLEX_BRIEF, files, evidence)

    assert "src/app/api/workouts/route.ts" in str(gap)
    assert "src/app/api/omnia/actions/route.ts" not in str(gap)
    assert "createMaxAction/getMaxActions" in str(gap)
