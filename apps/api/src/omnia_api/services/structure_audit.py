"""Lean, non-blocking structural audit for generated entity/app builds.

Entity/app builds are functional product screens assembled from the FIXED
component kit (AppShell / DataTable / CrudResource + shadcn primitives in
``src/components/{ui,omnia}``). They skip the acceptance gate (container-backed,
not static HTML), so this audit is the smoke detector: it flags the anti-patterns
the app-UI doctrine forbids — hardcoded colours instead of theme tokens,
fixed-pixel widths that break mobile, raw ``<table>``/``<aside>`` instead of the
kit, and single-page builds with no app shell.

It NEVER blocks a build — it returns warnings for logging/observability so drift
from the enterprise bar is visible. The kit + prompt doctrine + art-director
brief are the enforcement; this only reports.
"""

from __future__ import annotations

import re

# The writer's own app code only — never audit the fixed kit / engine / lib.
_FIXED_PREFIXES = (
    "src/components/ui/",
    "src/components/omnia/",
    "src/lib/",
    "src/app/api/",
)

# Hardcoded colour instead of a theme token (the doctrine bans these in app
# components — the art-director re-maps --primary, so tokens re-theme for free).
_HARDCODED_COLOR = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke)-(?:zinc|slate|gray|neutral|stone)-\d{2,3}\b"
    r"|\b(?:bg|text)-(?:white|black)\b"
)
# A fixed-pixel width on a container breaks mobile (use max-w-* / grid / %).
_FIXED_WIDTH = re.compile(r"\bw-\[\d{3,}px\]")
_RAW_TABLE = re.compile(r"<table\b")
_RAW_SIDEBAR = re.compile(r"<aside\b")
# A 3+ column grid with NO responsive variant anywhere in the file is a
# non-adaptive layout — it stays 3/4 columns on a 360px phone and overflows.
# Adaptive code is mobile-first: a `grid-cols-1` base + `sm:/md:/lg:grid-cols-N`.
_MULTICOL_GRID = re.compile(r"\bgrid-cols-[3-9]\b")
_RESPONSIVE_GRID = re.compile(r"\b(?:sm|md|lg|xl):grid-cols-\d")
_MOBILE_GRID_BASE = re.compile(r"\bgrid-cols-1\b")


def audit_entity_app(files: dict[str, str]) -> list[str]:
    """Return human-readable structural warnings for an entity/app build.

    Empty list = clean. Best-effort and side-effect-free; callers log the result
    but must never fail a build on it.
    """
    warnings: list[str] = []

    # Killer bug 1: writer rewrote globals.css with Tailwind v3 syntax (drops the
    # v4 @theme/token system) → build dies with "unknown utility class border-border".
    g = files.get("src/app/globals.css")
    if isinstance(g, str) and ("@tailwind " in g or "@apply border-border" in g):
        warnings.append(
            "src/app/globals.css: Tailwind v3 syntax (@tailwind/@apply border-border) — "
            "breaks the v4 build; globals.css must stay the fixed v4 token file (never rewrite)"
        )
    # Killer bug 2: starter page left alongside the (app) dashboard → two pages at "/".
    if "src/app/page.tsx" in files and "src/app/(app)/page.tsx" in files:
        warnings.append(
            "route conflict: src/app/page.tsx and src/app/(app)/page.tsx both resolve to '/' — "
            "delete the starter src/app/page.tsx"
        )

    app_tsx = {
        path: code
        for path, code in files.items()
        if isinstance(code, str)
        and path.endswith(".tsx")
        and not path.startswith(_FIXED_PREFIXES)
    }
    if not app_tsx:
        return warnings

    all_src = "\n".join(app_tsx.values())

    # App shell + multi-route: a real app wraps pages in <AppShell> (usually via a
    # `(app)` route-group layout). Its absence signals a thin single-page build.
    has_shell = "AppShell" in all_src or any("(app)/layout" in p for p in files)
    if not has_shell:
        warnings.append("no <AppShell> / (app) layout — looks single-page, not a real app")

    for path, code in sorted(app_tsx.items()):
        if _HARDCODED_COLOR.search(code):
            warnings.append(
                f"{path}: hardcoded colour — use theme tokens "
                "(bg-background/bg-card/text-muted-foreground/bg-primary/border-border)"
            )
        if _FIXED_WIDTH.search(code):
            warnings.append(f"{path}: fixed-px width container — breaks mobile, use max-w-*/grid")
        if (
            _MULTICOL_GRID.search(code)
            and not _RESPONSIVE_GRID.search(code)
            and not _MOBILE_GRID_BASE.search(code)
        ):
            warnings.append(
                f"{path}: non-adaptive grid (grid-cols-3/4 without grid-cols-1 sm:/lg:) — "
                "stays multi-column on a 360px phone; make it mobile-first"
            )
        if _RAW_TABLE.search(code):
            warnings.append(f"{path}: raw <table> — use <DataTable>/<CrudResource>")
        if _RAW_SIDEBAR.search(code) and "AppShell" not in code:
            warnings.append(f"{path}: hand-rolled <aside> sidebar — use <AppShell>")

    return warnings


__all__ = ["audit_entity_app"]
