from __future__ import annotations

from omnia_api.services.max_generation_contract import (
    build_max_product_contract,
    max_completion_gap,
    max_source_completion_gap,
    normalize_max_globals_css,
    requested_max_capabilities,
)

COMPLEX_BRIEF = """
ИИ тренер следит за сном и питанием, показывает статистику тренировок с графиком.
Нужны профиль пользователя, история действий, уведомления, loading/empty/error/retry.
"""


def _complete_files() -> dict[str, str]:
    return {
        "src/app/page.tsx": (
            'import { requestOmniaAI } from "@/lib/omnia/integration-client";\n'
            'import { createMaxAction } from "@/lib/omnia/client";\n'
            "export default function Page(){ return <main>ИИ тренер: тренировки, сон, "
            "питание, статистика, график, профиль, история, уведомления, loading, empty, "
            "error, retry</main>; }"
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


def _complete_single_file() -> dict[str, str]:
    """A real multi-view app may be intentionally cohesive in one component."""

    page = """"use client";
import { useState } from "react";
import { useMaxApp } from "@/components/MaxAppProvider";
import { createMaxAction, getMaxActions, requestOmniaAI } from "@/lib/omnia/integration-client";

export default function Page() {
  const { mode, user, error } = useMaxApp();
  const [view, setView] = useState("dashboard");
  const [loading, setLoading] = useState(false);
  const [empty, setEmpty] = useState(false);
  const [failure, setFailure] = useState(error || "");
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
    return {"src/app/page.tsx": page}


def test_contract_extracts_explicit_brief_and_forbids_fake_ai() -> None:
    labels = [label for _key, label, _needles in requested_max_capabilities(COMPLEX_BRIEF)]
    contract = build_max_product_contract(COMPLEX_BRIEF)

    assert "сон и восстановление" in labels
    assert "питание" in labels
    assert "уведомления" in labels
    assert "requestOmniaAI" in contract
    assert "fake timers" in contract
    assert "loading, empty, error/retry" in contract


def test_completion_rejects_untouched_canvas_and_thin_cosmetic_page() -> None:
    assert "no home page" in str(max_completion_gap(COMPLEX_BRIEF, {}, {})).lower()

    canvas = {"src/app/page.tsx": '<main data-testid="max-generation-canvas">Фитнес</main>'}
    assert "retired generation canvas" in str(max_completion_gap(COMPLEX_BRIEF, canvas, {}))

    thin = {
        "src/app/page.tsx": (
            "export default function Page(){return <main>ИИ тренер, тренировки, "
            "профиль, история</main>}"
        )
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


def test_completion_rejects_fake_ai_even_when_feature_words_exist() -> None:
    files = _complete_files()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        'import { requestOmniaAI } from "@/lib/omnia/integration-client";\n',
        "",
    )

    gap = max_completion_gap(
        COMPLEX_BRIEF,
        files,
        {"runtime_check_after_write": 1, "see_after_write": 1, "see": 2, "probe": 1},
    )

    assert gap is not None
    assert "requestOmniaAI" in gap


def test_completion_requires_only_max_compatible_runtime_proof() -> None:
    files = _complete_files()
    gap = max_completion_gap(COMPLEX_BRIEF, files, {})
    assert gap is not None
    assert "runtime_check" in gap

    evidence = {
        "build_after_write": 1,
        "runtime_check_after_write": 1,
        "see_after_write": 1,
    }
    assert max_completion_gap(COMPLEX_BRIEF, files, evidence) is None


def test_source_gap_is_independent_from_broken_max_preview_tooling() -> None:
    files = _complete_files()

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None
    assert (
        max_completion_gap(
            COMPLEX_BRIEF,
            files,
            {
                "runtime_check_after_write": 1,
                "see_after_write": 1,
                "probe_failed": 3,
                "see_failed": 3,
            },
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
            {"runtime_check_after_write": 1, "see_after_write": 1},
        )
        is None
    )


def test_managed_core_copy_cannot_fake_product_coverage() -> None:
    files = {
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
