"""Defect-class registry — the ratchet's anti-regression net (V1.6 1/5).

Each defect class has two fixtures: a CLEAN one (post-fix → 0 hits) and a
REVERTED one (the bug as it looked before the fix → exactly that class RED). The
reverted fixtures encode the plan's guarantee: "revert any past fix → the
matching assert goes RED". A clean full-app fixture proves zero false positives.
"""

from omnia_api.services import defect_registry as dr
from omnia_api.services.defect_registry import scan


def _classes(files: dict[str, str]) -> set[str]:
    return {d.defect_class for d in scan(files).defects}


# ── 1. dead-auth-link ─────────────────────────────────────────────────────────


def test_dead_auth_link_clean():
    files = {"app/page.tsx": '<Link href="/signin">Войти</Link>'}
    assert dr.DEAD_AUTH_LINK not in _classes(files)


def test_dead_auth_link_reverted_is_red():
    files = {"app/page.tsx": '<Link href="/">Войти</Link>'}
    assert dr.DEAD_AUTH_LINK in _classes(files)


def test_dead_auth_link_ignores_non_auth_self_links():
    # Logo / "на главную" linking to "/" is correct, not a defect.
    files = {"app/page.tsx": '<Link href="/">На главную</Link>'}
    assert dr.DEAD_AUTH_LINK not in _classes(files)


def test_dead_auth_link_in_freeform_html_anchor():
    files = {"index.html": '<a href="#">Войти</a>'}
    assert dr.DEAD_AUTH_LINK in _classes(files)


# ── 2. invented-palette-var ───────────────────────────────────────────────────


def test_invented_palette_var_clean_when_declared():
    files = {"app/globals.css": ":root{--brand:#f00}\n.x{color:var(--brand)}"}
    assert dr.INVENTED_PALETTE_VAR not in _classes(files)


def test_invented_palette_var_clean_for_known_role_alias():
    # `--primary` is a recognised palette alias even with no explicit :root decl.
    files = {"app/globals.css": ".x{color:var(--primary)}"}
    assert dr.INVENTED_PALETTE_VAR not in _classes(files)


def test_invented_palette_var_reverted_is_red():
    files = {"app/globals.css": ".x{color:var(--brand-glow-7)}"}
    assert dr.INVENTED_PALETTE_VAR in _classes(files)


def test_invented_palette_var_ignores_framework_vars():
    files = {"app/globals.css": ".x{transform:var(--tw-translate-x)}"}
    assert dr.INVENTED_PALETTE_VAR not in _classes(files)


def test_invented_palette_var_cross_file_declaration():
    # Declared in globals.css, used in a component → fine.
    files = {
        "app/globals.css": ":root{--hero-mesh:#123}",
        "app/page.tsx": "style={{background:'var(--hero-mesh)'}}",
    }
    assert dr.INVENTED_PALETTE_VAR not in _classes(files)


# ── 3. invalid-lucide-import ──────────────────────────────────────────────────


def test_invalid_lucide_import_clean():
    files = {"app/page.tsx": 'import { Briefcase } from "lucide-react";'}
    assert dr.INVALID_LUCIDE_IMPORT not in _classes(files)


def test_invalid_lucide_import_reverted_is_red():
    files = {"app/page.tsx": 'import { Dashboard } from "lucide-react";'}
    assert dr.INVALID_LUCIDE_IMPORT in _classes(files)


def test_invalid_lucide_import_handles_alias_form():
    files = {"app/page.tsx": 'import { Briefcase as Bag } from "lucide-react";'}
    assert dr.INVALID_LUCIDE_IMPORT not in _classes(files)


# ── 4. missing-lucide-import ──────────────────────────────────────────────────


def test_missing_lucide_import_clean():
    files = {
        "app/page.tsx": 'import { Briefcase } from "lucide-react";\nconst X = () => <Briefcase/>;'
    }
    assert dr.MISSING_LUCIDE_IMPORT not in _classes(files)


def test_missing_lucide_import_reverted_is_red():
    # Used in JSX, imported nowhere → "Briefcase is not defined" at runtime.
    files = {"app/page.tsx": "const X = () => <Briefcase/>;"}
    assert dr.MISSING_LUCIDE_IMPORT in _classes(files)


def test_missing_lucide_import_ignores_jsdoc_example():
    # Kit components document props with `e.g. <User />` — a comment, not a render.
    files = {
        "src/components/omnia/stat-card.tsx": (
            "/** A lucide icon element, e.g. `<Users />`. */\n"
            "export function StatCard({ icon }) { return <div>{icon}</div>; }"
        )
    }
    assert dr.MISSING_LUCIDE_IMPORT not in _classes(files)


def test_missing_lucide_import_skips_locally_bound_name():
    # `Badge` is a lucide export but here it's a kit component import → not missing.
    files = {
        "app/page.tsx": 'import { Badge } from "@/components/ui/badge";\nconst X = () => <Badge/>;'
    }
    assert dr.MISSING_LUCIDE_IMPORT not in _classes(files)


# ── 5. misrouted-kit-import ───────────────────────────────────────────────────


def test_misrouted_kit_import_clean():
    files = {"app/page.tsx": 'import { toast } from "sonner";'}
    assert dr.MISROUTED_KIT_IMPORT not in _classes(files)


def test_misrouted_kit_import_reverted_is_red():
    files = {"app/page.tsx": 'import { formatDate, toast } from "@/lib/utils";'}
    assert dr.MISROUTED_KIT_IMPORT in _classes(files)


# ── 6. dark-theme-not-on-dashboard (kit capability) ───────────────────────────

_APP_SHELL_GOOD = (
    'export interface AppShellProps { theme?: "light" | "dark"; }\n'
    'const dark = theme === "dark";\n'
    'root.classList.add("dark");\n'
)


def test_dark_theme_capability_clean():
    files = {"src/components/omnia/app-shell.tsx": _APP_SHELL_GOOD}
    assert dr.DARK_THEME_NOT_ON_DASHBOARD not in _classes(files)


def test_dark_theme_capability_reverted_prop_removed_is_red():
    files = {"src/components/omnia/app-shell.tsx": "export interface AppShellProps {}\n"}
    assert dr.DARK_THEME_NOT_ON_DASHBOARD in _classes(files)


def test_dark_theme_capability_reverted_html_mirror_removed_is_red():
    # prop kept, html-mirror gone
    body = 'export interface AppShellProps { theme?: "light" | "dark"; }\n'
    files = {"src/components/omnia/app-shell.tsx": body}
    assert dr.DARK_THEME_NOT_ON_DASHBOARD in _classes(files)


def test_dark_theme_capability_noop_without_kit_file():
    # Model-answer-only scan (no kit files) must not fire kit-integrity asserts.
    files = {"app/page.tsx": "export default function Page(){return null}"}
    assert dr.DARK_THEME_NOT_ON_DASHBOARD not in _classes(files)


# ── 7. ru-hero-word-clip (kit capability) ─────────────────────────────────────


def test_ru_hero_word_clip_clean():
    files = {"app/globals.css": "h1,h2{overflow-wrap:break-word;hyphens:auto;}"}
    assert dr.RU_HERO_WORD_CLIP not in _classes(files)


def test_ru_hero_word_clip_reverted_is_red():
    files = {"app/globals.css": "h1,h2{font-weight:700;}"}
    assert dr.RU_HERO_WORD_CLIP in _classes(files)


# ── 8. empty-public-catalog ───────────────────────────────────────────────────


def test_empty_catalog_clean():
    files = {
        "app/catalog/page.tsx": "<img data-omnia-gen={`product shot, ${i.name}, studio`} />"
    }
    assert dr.EMPTY_PUBLIC_CATALOG not in _classes(files)


def test_empty_catalog_reverted_is_red():
    # All-interpolation prompt → resolver skips it → empty card.
    files = {"app/catalog/page.tsx": "<img data-omnia-gen={`${item.name}`} />"}
    assert dr.EMPTY_PUBLIC_CATALOG in _classes(files)


# ── 9. toast-popover-transparent (kit capability) ─────────────────────────────

_SONNER_HOST = "src/components/ui/sonner.tsx"


def test_toast_popover_clean_when_tokens_present():
    files = {
        _SONNER_HOST: 'style={{"--normal-bg": "var(--popover)"}}',
        "app/globals.css": ":root{--popover:oklch(1 0 0);--popover-foreground:oklch(0.18 0 0)}",
    }
    assert dr.TOAST_POPOVER_TRANSPARENT not in _classes(files)


def test_toast_popover_reverted_dropped_token_is_red():
    # Brand re-map kept --primary but dropped the popover surface tokens the
    # toast paints from → every toast renders transparent.
    files = {
        _SONNER_HOST: 'style={{"--normal-bg": "var(--popover)"}}',
        "app/globals.css": ":root{--primary:#f00}",
    }
    assert dr.TOAST_POPOVER_TRANSPARENT in _classes(files)


def test_toast_popover_noop_without_toast_host():
    # No sonner host shipped (freeform/static app) → nothing to guard.
    files = {"app/globals.css": ":root{--primary:#f00}"}
    assert dr.TOAST_POPOVER_TRANSPARENT not in _classes(files)


# ── 10. reduced-motion-missing (WCAG opt-out) ─────────────────────────────────

_ANIM = (
    "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}\n"
    ".x{animation:spin 2s linear infinite}"
)
_REDUCE_RESET = (
    "@media (prefers-reduced-motion: reduce)"
    "{*{animation:none!important;transition:none!important}}"
)


def test_reduced_motion_clean_no_preference_gating():
    # The shipped-kit pattern: every @keyframes/animation lives inside a
    # `prefers-reduced-motion: no-preference` block → nothing animates under reduce.
    files = {"app/globals.css": "@media (prefers-reduced-motion: no-preference){\n" + _ANIM + "\n}"}
    assert dr.REDUCED_MOTION_MISSING not in _classes(files)


def test_reduced_motion_clean_reduce_neutraliser():
    # The alternate valid pattern: animate freely, then kill motion under reduce.
    files = {"app/globals.css": _ANIM + "\n" + _REDUCE_RESET}
    assert dr.REDUCED_MOTION_MISSING not in _classes(files)


def test_reduced_motion_reverted_is_red():
    # Adversarial fixture: unconditional animation, zero opt-out → must fail.
    files = {"app/globals.css": _ANIM}
    assert dr.REDUCED_MOTION_MISSING in _classes(files)


def test_reduced_motion_reverted_when_no_preference_wrapper_stripped():
    # Strip the no-preference @media wrapper → top-level animation, no opt-out → RED.
    files = {"app/globals.css": _ANIM}
    assert dr.REDUCED_MOTION_MISSING in _classes(files)


def test_reduced_motion_ignores_bare_transition():
    # Hover transitions (no @keyframes/animation) are not hypnotic motion → no flag.
    files = {"app/globals.css": ".btn{transition:transform .2s ease, box-shadow .2s ease}"}
    assert dr.REDUCED_MOTION_MISSING not in _classes(files)


def test_reduced_motion_ignores_non_css_files():
    # JS reading prefers-reduced-motion at runtime is not a CSS opt-out concern.
    js = "const reduce = matchMedia('(prefers-reduced-motion: reduce)')"
    files = {"src/components/omnia/count-up.tsx": js}
    assert dr.REDUCED_MOTION_MISSING not in _classes(files)


def test_reduced_motion_universal_reset_covers_other_files():
    # A universal reduce reset in one file neutralises motion app-wide.
    files = {
        "app/globals.css": _REDUCE_RESET,
        "index.html": "<style>" + _ANIM + "</style>",
    }
    assert dr.REDUCED_MOTION_MISSING not in _classes(files)


def test_reduced_motion_shipped_kit_globals_is_clean():
    # Hard regression guard: the real product kit must always pass its own gate.
    import pathlib

    kit = (
        pathlib.Path(__file__).resolve().parents[2]
        / "orchestrator"
        / "templates"
        / "nextjs-entities"
        / "src"
        / "app"
        / "globals.css"
    )
    if not kit.exists():  # pragma: no cover - layout-dependent
        return
    files = {"app/globals.css": kit.read_text(encoding="utf-8")}
    assert dr.REDUCED_MOTION_MISSING not in _classes(files)


# ── registry contract ─────────────────────────────────────────────────────────


def test_clean_full_app_passes_with_zero_false_positives():
    files = {
        "app/page.tsx": (
            'import Link from "next/link";\n'
            'import { Briefcase } from "lucide-react";\n'
            'import { toast } from "sonner";\n'
            'const X = () => <Briefcase/>;\n'
            '<Link href="/signup">Начать</Link>'
        ),
        "app/globals.css": ":root{--brand:#f00}\nh1,h2{overflow-wrap:break-word;hyphens:auto;}",
        "src/components/omnia/app-shell.tsx": _APP_SHELL_GOOD,
        "app/catalog/page.tsx": "<img data-omnia-gen={`shot, ${i.name}, studio`} />",
    }
    report = scan(files)
    assert report.passed, report.summary()
    assert report.classes == ()


def test_report_classes_in_registry_order():
    files = {
        "app/catalog/page.tsx": "<img data-omnia-gen={`${i.name}`} />",
        "app/page.tsx": 'import Link from "next/link";\n<Link href="/">Войти</Link>',
    }
    report = scan(files)
    assert report.classes == (dr.DEAD_AUTH_LINK, dr.EMPTY_PUBLIC_CATALOG)
    assert not report.passed


def test_scan_is_fail_soft_on_pathological_input():
    # Empty / odd content must never raise.
    assert scan({}).passed
    assert scan({"x.tsx": ""}).passed
