from __future__ import annotations

from omnia_api.services.max_generation_contract import (
    build_max_product_contract,
    max_completion_gap,
    max_source_completion_gap,
    normalize_max_globals_css,
    requested_max_capabilities,
    unsafe_max_backend_paths,
)

COMPLEX_BRIEF = """
ИИ тренер следит за сном и питанием, показывает статистику тренировок с графиком.
Нужны профиль пользователя, история действий, уведомления, loading/empty/error/retry.
"""


def _complete_files() -> dict[str, str]:
    return {
        "src/app/page.tsx": (
            "import { requestOmniaAI, createMaxAction, getMaxActions } "
            'from "@/lib/omnia/integration-client";\n'
            "async function load(){ return (await getMaxActions()).actions; }\n"
            'async function save(){ return createMaxAction("health_saved", {}); }\n'
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


def test_contract_extracts_explicit_brief_and_forbids_fake_ai() -> None:
    labels = [label for _key, label, _needles in requested_max_capabilities(COMPLEX_BRIEF)]
    contract = build_max_product_contract(COMPLEX_BRIEF)

    assert "сон и восстановление" in labels
    assert "питание" in labels
    assert "уведомления" in labels
    assert "requestOmniaAI" in contract
    assert "fake timers" in contract
    assert "loading, empty, error/retry" in contract
    assert "Never fabricate the current user's history" in contract
    assert "honest empty/onboarding state" in contract
    assert "getMaxActions({ limit, cursor })" in contract


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
        "requestOmniaAI, ",
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


def test_completion_rejects_product_db_bypass_but_allows_managed_routes() -> None:
    files = _complete_files()
    files["src/app/api/workouts/route.ts"] = (
        'import { db } from "@/lib/db";\n'
        'import { workouts } from "@/lib/db/schema";\n'
        "export async function POST() {\n"
        "  return db.insert(workouts).values({ title: 'x' });\n"
        "}"
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
    assert "requireMaxUser()" in str(gap)
    assert "maxUserId: user.id" in str(gap)


def test_completion_allows_direct_db_when_max_user_scope_is_verified() -> None:
    files = _complete_files()
    files["src/app/api/workouts/route.ts"] = (
        'import { db } from "@/lib/db";\n'
        'import { workouts } from "@/lib/db/schema";\n'
        'import { requireMaxUser } from "@/lib/max/session";\n'
        "export async function POST() {\n"
        "  const user = await requireMaxUser();\n"
        "  return db.insert(workouts).values({ maxUserId: user.id, title: 'x' });\n"
        "}"
    )

    evidence = {
        "build_after_write": 1,
        "runtime_check_after_write": 1,
        "see_after_write": 1,
    }

    assert max_completion_gap(COMPLEX_BRIEF, files, evidence) is None


def test_unsafe_backend_paths_only_flag_unscoped_direct_db() -> None:
    files = {
        "src/app/api/workouts/route.ts": (
            'import { db } from "@/lib/db";\n'
            'import { workouts } from "@/lib/db/schema";\n'
            "export async function GET() {\n"
            "  return db.select().from(workouts);\n"
            "}"
        ),
        "src/app/api/safe/route.ts": (
            'import { db } from "@/lib/db";\n'
            'import { workouts } from "@/lib/db/schema";\n'
            'import { requireMaxUser } from "@/lib/max/session";\n'
            "export async function GET() {\n"
            "  const user = await requireMaxUser();\n"
            "  return db.select().from(workouts).where(eq(workouts.maxUserId, user.id));\n"
            "}"
        ),
    }

    assert unsafe_max_backend_paths(files) == ["src/app/api/workouts/route.ts"]


def test_managed_scaffold_does_not_fake_product_persistence_usage() -> None:
    files = _complete_files()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "import { requestOmniaAI, createMaxAction, getMaxActions } "
        'from "@/lib/omnia/integration-client";\n',
        'import { requestOmniaAI } from "@/lib/omnia/integration-client";\n',
    )
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "async function load(){ return (await getMaxActions()).actions; }\n"
        'async function save(){ return createMaxAction("health_saved", {}); }\n',
        "",
    )
    files["src/lib/omnia/integration-client.ts"] = (
        "export async function createMaxAction(){}\n"
        "export async function getMaxActions(){ return {actions: []}; }"
    )

    gap = max_source_completion_gap(COMPLEX_BRIEF + "\nСохранять результаты.", files)

    assert "createMaxAction" in str(gap)
    assert "getMaxActions" in str(gap)
    assert "scaffold definitions do not count" in str(gap)


def test_managed_scaffold_does_not_fake_product_ai_usage() -> None:
    files = _complete_files()
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "requestOmniaAI, ",
        "",
    )
    files["src/lib/omnia/integration-client.ts"] = (
        "export async function requestOmniaAI(){ return {answer: 'managed'}; }"
    )

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert "requestOmniaAI" in str(gap)


def test_completion_rejects_demo_or_hardcoded_personal_state() -> None:
    files = _complete_files()
    files["src/lib/fitness/data.ts"] = """
// Demo dataset for the fitness MAX mini app.
export const WORKOUTS = [{ id: "w-1", date: "Сегодня", volumeKg: 8420 }];
export const MEALS_TODAY = [{ title: "Овсянка", kcal: 520 }];
export const READINESS_CURRENT = 76;
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert "src/lib/fitness/data.ts" in str(gap)
    assert "demo/test or hardcoded personal user data" in str(gap)
    assert "honest empty/onboarding" in str(gap)


def test_completion_allows_static_immutable_reference_catalog() -> None:
    files = _complete_files()
    files["src/lib/fitness/reference-exercises.ts"] = """
export const REFERENCE_EXERCISES = [
  { id: "squat", name: "Приседания со штангой", group: "Ноги" },
  { id: "deadlift", name: "Становая тяга", group: "Спина / ноги" },
];
"""

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_completion_allows_labeled_sample_reference_catalog() -> None:
    files = _complete_files()
    files["src/lib/fitness/reference-exercises.ts"] = """
// Sample dataset for the immutable exercise taxonomy.
export const REFERENCE_EXERCISES = [
  { id: "squat", name: "Приседания со штангой", group: "Ноги" },
];
"""

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_completion_allows_generic_records_in_explicit_reference_file() -> None:
    files = _complete_files()
    files["src/lib/fitness/reference-sessions.ts"] = """
export const sessions = [{ id: "template-1", title: "Базовая силовая сессия" }];
"""

    assert max_source_completion_gap(COMPLEX_BRIEF, files) is None


def test_completion_rejects_hardcoded_personal_state_without_demo_label() -> None:
    files = _complete_files()
    files["src/lib/fitness/data.ts"] = """
export const WEEK_LOAD = [{ day: "Пн", load: 95 }];
export const READINESS_CURRENT = 76;
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert "hardcoded personal user data" in str(gap)


def test_completion_rejects_lowercase_hardcoded_workouts() -> None:
    files = _complete_files()
    files["src/lib/fitness/data.ts"] = """
export const workouts = [{ id: "w-1", kg: 1200 }];
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert "hardcoded personal user data" in str(gap)


def test_completion_rejects_generic_hardcoded_profile_and_history() -> None:
    files = _complete_files()
    files["src/lib/fitness/user-state.ts"] = """
export const profile = { name: "Alex" };
export const history = [{ id: "h-1", title: "Прошлая тренировка" }];
"""

    gap = max_source_completion_gap(COMPLEX_BRIEF, files)

    assert "hardcoded personal user data" in str(gap)


def test_read_only_user_data_requires_read_but_not_write() -> None:
    prompt = "Покажи профиль пользователя, историю действий и статистику."
    files = _complete_files()
    files["src/app/page.tsx"] = (
        files["src/app/page.tsx"]
        .replace(
            ", createMaxAction, getMaxActions",
            ", getMaxActions",
        )
        .replace(
            'async function save(){ return createMaxAction("health_saved", {}); }\n',
            "",
        )
    )

    assert max_source_completion_gap(prompt, files) is None


def test_mutating_user_data_requires_write() -> None:
    prompt = "Покажи историю тренировок и сохраняй новую тренировку."
    files = _complete_files()
    files["src/app/page.tsx"] = (
        files["src/app/page.tsx"]
        .replace(
            ", createMaxAction, getMaxActions",
            ", getMaxActions",
        )
        .replace(
            'async function save(){ return createMaxAction("health_saved", {}); }\n',
            "",
        )
    )

    gap = max_source_completion_gap(prompt, files)

    assert "createMaxAction" in str(gap)
