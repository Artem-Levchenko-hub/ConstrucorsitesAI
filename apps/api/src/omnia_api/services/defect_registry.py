"""Defect-class registry — the deterministic ratchet of the acceptance gauntlet.

V1.6 slice 1/5. One read-only harness that scans a generated app's source files
for every generator-wide defect class we have ever fixed, and reports any that
reappear. Run it against a FRESH generation on each niche-E2E and on each change
to the generator; a clean (empty) report is what lets a V-task be marked done.

The point is anti-regression with TEETH: each closed defect lives here as ONE
deterministic assert (R-04 — knowledge in a single place, not re-described in
prose memory N times). Revert any past fix and the matching detector goes RED,
so a fix can never silently rot back in across niches. New niche surfaces a new
class → it MUST add a detector here (that is the "delta" a niche contributes).

Detection reuses the SAME constants the fix-guards use (``file_extractor``,
``palette_guard``, ``image_resolver``, ``lucide_icon_names``) so the registry and
the repair can never drift apart — there is exactly one definition of each
defect's shape. Every detector is pure, fail-soft (an unparseable file is
skipped, never raised), idempotent, and side-effect-free (R-10).

Two families of detector:

  * OUTPUT defects — present in the model's authored files (dead auth CTA,
    invalid/missing lucide import, misrouted kit import, invented palette var,
    empty catalog image). These fire on any file set, including the model's raw
    ``<file>`` answer.
  * KIT-INTEGRITY defects — a shipped kit capability was removed (the AppShell
    dark-theme prop, the globals.css hero-word-break rule, the popover surface
    tokens the sonner toast paints from). These fire only when the full
    provisioned app dir is scanned (the gauntlet's intended input); they no-op
    when the kit files aren't in the set.

Request↔output FIDELITY (did a dark-theme *request* actually reach the
dashboard) is deliberately out of scope here — that is the chip→pixel gate
(V1.6 slice 4/5 / V2.5), which has the discovery answers this registry doesn't.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from omnia_api.services.file_extractor import (
    _ANY_NAMED_IMPORT,
    _AS_SPLIT,
    _AUTH_LINK_EL,
    _IDENT,
    _IMPORT_STMT,
    _JSX_TAG,
    _KIT_SYMBOL_CANONICAL_SOURCE,
    _LOCAL_DECL,
    _LUCIDE_IMPORT_BLOCK,
    _TAG_STRIP,
    _WS,
    _auth_link_dest,
    _imported_name,
    _iter_specs,
)
from omnia_api.services.image_resolver import _TAG_RE_EXPR, _static_prompt_from_expr
from omnia_api.services.lucide_icon_names import is_valid_lucide_name
from omnia_api.services.palette_guard import _VAR_ROLE

log = logging.getLogger(__name__)

# Stable ids — the vocabulary of the ratchet's register. Keep in sync with the
# plan (§5★ V1.6 1/5). A new detector → append its id here.
DEAD_AUTH_LINK = "dead-auth-link"
INVENTED_PALETTE_VAR = "invented-palette-var"
INVALID_LUCIDE_IMPORT = "invalid-lucide-import"
MISSING_LUCIDE_IMPORT = "missing-lucide-import"
MISROUTED_KIT_IMPORT = "misrouted-kit-import"
DARK_THEME_NOT_ON_DASHBOARD = "dark-theme-not-on-dashboard"
RU_HERO_WORD_CLIP = "ru-hero-word-clip"
EMPTY_PUBLIC_CATALOG = "empty-public-catalog"
TOAST_POPOVER_TRANSPARENT = "toast-popover-transparent"

DEFECT_CLASSES: tuple[str, ...] = (
    DEAD_AUTH_LINK,
    INVENTED_PALETTE_VAR,
    INVALID_LUCIDE_IMPORT,
    MISSING_LUCIDE_IMPORT,
    MISROUTED_KIT_IMPORT,
    DARK_THEME_NOT_ON_DASHBOARD,
    RU_HERO_WORD_CLIP,
    EMPTY_PUBLIC_CATALOG,
    TOAST_POPOVER_TRANSPARENT,
)

# File families. Auth CTAs live in JSX *and* in freeform `<a>` HTML.
_SOURCE_SUFFIXES = (".tsx", ".jsx", ".ts")
_AUTH_SUFFIXES = (".tsx", ".jsx", ".html", ".htm")
_CSS_SUFFIXES = (".css", ".html", ".htm")

# A `var(--x)` usage and a `--x:` declaration, anywhere (CSS, inline <style>, or a
# JSX `style={{ … 'var(--x)' … }}` object).
_VAR_USE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*[,)]")
_VAR_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
# Framework-managed custom props that are declared at runtime, not in source —
# never an "invented" var even though no `--x:` line exists for them.
_VAR_FRAMEWORK_PREFIXES = ("--tw-", "--radix-", "--shiki-")

# Block / line comments. JSDoc routinely shows JSX examples (`e.g. <User />`) that
# are not real renders — stripped before hunting for used-but-unimported glyphs so
# a documentation example never reads as a runtime crash.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")


def _strip_comments(body: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", body))


@dataclass(frozen=True)
class Defect:
    """One reappearance of a known defect class in a generated file."""

    defect_class: str
    path: str
    detail: str


@dataclass(frozen=True)
class DefectReport:
    """Verdict of one registry scan."""

    defects: tuple[Defect, ...]

    @property
    def passed(self) -> bool:
        return not self.defects

    @property
    def classes(self) -> tuple[str, ...]:
        """Distinct defect classes that fired, in registry order."""
        hit = {d.defect_class for d in self.defects}
        return tuple(c for c in DEFECT_CLASSES if c in hit)

    def summary(self) -> str:
        if self.passed:
            return "defect-registry: clean (0 known defect classes)"
        lines = [
            f"defect-registry: {len(self.defects)} issue(s) "
            f"across {len(self.classes)} class(es):"
        ]
        for d in self.defects:
            lines.append(f"  [{d.defect_class}] {d.path}: {d.detail}")
        return "\n".join(lines)


# ── OUTPUT detectors ─────────────────────────────────────────────────────────


def _dead_auth_links(path: str, body: str) -> list[Defect]:
    """A login/sign-up CTA pointing at a dead self-link (`/`, `#`, empty).

    Mirror of ``file_extractor._fix_dead_auth_links``: ``_AUTH_LINK_EL`` already
    matches only the dead-href shape, so any match whose visible text reads as an
    auth affordance is the defect.
    """
    out: list[Defect] = []
    for m in _AUTH_LINK_EL.finditer(body):
        text = _WS.sub(" ", _TAG_STRIP.sub(" ", m.group("inner"))).strip().lower()
        if _auth_link_dest(text) is not None:
            out.append(Defect(DEAD_AUTH_LINK, path, f"auth CTA «{text[:48]}» → dead self-link"))
    return out


def _invalid_lucide_imports(path: str, body: str) -> list[Defect]:
    """A name imported from ``lucide-react`` that is not a real lucide export."""
    if "lucide-react" not in body:
        return []
    out: list[Defect] = []
    for block in _LUCIDE_IMPORT_BLOCK.finditer(body):
        for spec in block.group("names").split(","):
            spec = spec.strip()
            if not spec:
                continue
            name = _AS_SPLIT.split(spec, 1)[0].strip()
            if not is_valid_lucide_name(name):
                out.append(Defect(INVALID_LUCIDE_IMPORT, path, f"unknown lucide icon «{name}»"))
    return out


def _missing_lucide_imports(path: str, body: str) -> list[Defect]:
    """A real lucide glyph rendered in JSX but bound nowhere → runtime crash.

    Same shape as ``file_extractor._fix_missing_lucide_imports``: a capitalised
    JSX tag that is a valid lucide export and is neither imported nor locally
    declared throws ``<Name> is not defined``. Comments are stripped first so a
    JSDoc example (``e.g. <User />``) is never mistaken for a real render.
    """
    body = _strip_comments(body)
    candidates = {
        m.group(1) for m in _JSX_TAG.finditer(body) if is_valid_lucide_name(m.group(1))
    }
    if not candidates:
        return []
    bound: set[str] = set()
    for stmt in _IMPORT_STMT.finditer(body):
        bound.update(_IDENT.findall(stmt.group("binding")))
    bound.update(_LOCAL_DECL.findall(body))
    return [
        Defect(MISSING_LUCIDE_IMPORT, path, f"icon <{name}/> used but never imported")
        for name in sorted(candidates - bound)
    ]


def _misrouted_kit_imports(path: str, body: str) -> list[Defect]:
    """A kit symbol imported from the wrong module (e.g. ``toast`` ← utils).

    Reuses ``_KIT_SYMBOL_CANONICAL_SOURCE``: a symbol with a known canonical home
    imported from a different module fails the build's static export check.
    """
    if not any(sym in body for sym in _KIT_SYMBOL_CANONICAL_SOURCE):
        return []
    out: list[Defect] = []
    for m in _ANY_NAMED_IMPORT.finditer(body):
        module = m.group("module")
        for spec in _iter_specs(m.group("names")):
            name = _imported_name(spec)
            canonical = _KIT_SYMBOL_CANONICAL_SOURCE.get(name)
            if canonical is not None and canonical != module:
                out.append(
                    Defect(
                        MISROUTED_KIT_IMPORT,
                        path,
                        f"«{name}» imported from «{module}» (belongs to «{canonical}»)",
                    )
                )
    return out


def _empty_catalog_images(path: str, body: str) -> list[Defect]:
    """A JSX catalog image whose prompt is *only* interpolation → no image.

    Root cause of the empty-public-catalog defect (ce9cc07): a
    ``data-omnia-gen={`…${x}…`}`` tag whose static descriptors collapse to ""
    is skipped by the resolver, so the catalog card renders no image.
    """
    out: list[Defect] = []
    for m in _TAG_RE_EXPR.finditer(body):
        if not _static_prompt_from_expr(m.group(2)):
            out.append(
                Defect(
                    EMPTY_PUBLIC_CATALOG,
                    path,
                    "catalog <img data-omnia-gen> has no static prompt (all interpolation)",
                )
            )
    return out


def _invented_palette_vars(files: dict[str, str]) -> list[Defect]:
    """``var(--x)`` where ``--x`` is declared nowhere and is not a known role.

    Cross-file: a var declared in ``globals.css`` and used in a component is fine.
    Conservative — a usage is flagged only when it is undeclared everywhere AND
    not a recognised palette alias (``_VAR_ROLE``) AND not framework-managed
    (``--tw-*`` / ``--radix-*``), so legitimate kit/Tailwind vars never trip it.
    """
    declared: set[str] = set()
    for content in files.values():
        declared.update(d.lower() for d in _VAR_DECL_RE.findall(content))

    out: list[Defect] = []
    seen: set[tuple[str, str]] = set()
    for path, content in files.items():
        if not path.lower().endswith(_CSS_SUFFIXES) and "var(--" not in content:
            continue
        for m in _VAR_USE_RE.finditer(content):
            name = m.group(1).lower()
            if name in declared or name in _VAR_ROLE:
                continue
            if name.startswith(_VAR_FRAMEWORK_PREFIXES):
                continue
            key = (path, name)
            if key in seen:
                continue
            seen.add(key)
            out.append(Defect(INVENTED_PALETTE_VAR, path, f"var({name}) is never declared"))
    return out


# ── KIT-INTEGRITY detectors (fire only when the kit file is in the set) ───────


def _dark_theme_capability(files: dict[str, str]) -> list[Defect]:
    """The AppShell must keep the dark-theme capability that fixed b06e6f6.

    Without the ``theme`` prop AND the ``<html>`` dark-class mirror, a dark-theme
    request can never reach the dashboard surface (Radix portals escape the
    wrapper) — the exact "landing dark, dashboard light" regression. We assert the
    shipped capability survives; the request→delivery fidelity is the chip→pixel
    gate's job, not this one.
    """
    out: list[Defect] = []
    for path, body in files.items():
        if not path.endswith("app-shell.tsx"):
            continue
        if 'theme?:' not in body or '"dark"' not in body:
            out.append(
                Defect(DARK_THEME_NOT_ON_DASHBOARD, path, "AppShell lost its `theme` prop")
            )
        elif 'classList.add("dark")' not in body:
            out.append(
                Defect(
                    DARK_THEME_NOT_ON_DASHBOARD,
                    path,
                    "AppShell no longer mirrors `dark` onto <html> (portals stay light)",
                )
            )
    return out


def _ru_hero_word_break(files: dict[str, str]) -> list[Defect]:
    """globals.css must keep the h1/h2 word-break rule that fixed RU hero clip.

    A long Russian compound heading overflows its column and clips without
    ``overflow-wrap: break-word`` + ``hyphens: auto`` on headings (43d1c5d).
    """
    out: list[Defect] = []
    for path, body in files.items():
        if not path.endswith("globals.css"):
            continue
        if "break-word" not in body or "hyphens" not in body:
            out.append(
                Defect(
                    RU_HERO_WORD_CLIP,
                    path,
                    "globals.css lost the h1/h2 overflow-wrap/hyphens rule",
                )
            )
    return out


def _toast_popover_token(files: dict[str, str]) -> list[Defect]:
    """The themed sonner toast needs the popover surface tokens it paints from.

    ``components/ui/sonner.tsx`` paints the toast surface from ``var(--popover)``
    / ``var(--popover-foreground)`` (mirrored onto the ``bg-popover`` /
    ``text-popover-foreground`` utilities). If the kit ships that toast host but a
    brand re-map of ``:root`` drops those tokens from globals.css, ``--normal-bg``
    resolves to nothing and every toast renders transparent — unreadable over the
    page. Kit-integrity check, like the dark-theme prop: fires only when the toast
    host is in the scanned set; no host shipped (freeform/static app) → no-op.
    """
    if not any(p.endswith("components/ui/sonner.tsx") for p in files):
        return []
    out: list[Defect] = []
    for path, body in files.items():
        if not path.endswith("globals.css"):
            continue
        missing = [tok for tok in ("--popover:", "--popover-foreground:") if tok not in body]
        if missing:
            out.append(
                Defect(
                    TOAST_POPOVER_TRANSPARENT,
                    path,
                    "globals.css dropped "
                    + ", ".join(missing)
                    + " → sonner toast renders transparent",
                )
            )
    return out


# ── orchestration ────────────────────────────────────────────────────────────

# (suffix-scoped output detector) -> applicable suffixes
_PER_FILE_DETECTORS: tuple[tuple[object, tuple[str, ...]], ...] = (
    (_dead_auth_links, _AUTH_SUFFIXES),
    (_invalid_lucide_imports, _SOURCE_SUFFIXES),
    (_missing_lucide_imports, _SOURCE_SUFFIXES),
    (_misrouted_kit_imports, _SOURCE_SUFFIXES),
    (_empty_catalog_images, _SOURCE_SUFFIXES),
)
_WHOLE_SET_DETECTORS = (
    _invented_palette_vars,
    _dark_theme_capability,
    _ru_hero_word_break,
    _toast_popover_token,
)


def scan(files: dict[str, str]) -> DefectReport:
    """Scan a {path: content} map for every known generator-wide defect class.

    Pure and fail-soft: a detector that raises on one pathological file is logged
    and skipped, never aborting the scan (R-10). Returns a ``DefectReport`` whose
    ``passed`` is True iff zero known defects reappeared.
    """
    defects: list[Defect] = []
    for path, body in files.items():
        low = path.lower()
        for detector, suffixes in _PER_FILE_DETECTORS:
            if not low.endswith(suffixes):
                continue
            try:
                defects.extend(detector(path, body))  # type: ignore[operator]
            except Exception as exc:
                log.warning("defect_registry: %s failed on %s (skipped): %r", detector, path, exc)
    for whole in _WHOLE_SET_DETECTORS:
        try:
            defects.extend(whole(files))
        except Exception as exc:
            log.warning("defect_registry: %s failed (skipped): %r", whole, exc)
    return DefectReport(defects=tuple(defects))


# ── CLI: scan a provisioned app directory (wireable into /safe-commit) ────────

_SCANNABLE_SUFFIXES = (".tsx", ".jsx", ".ts", ".css", ".html", ".htm")
_SKIP_DIRS = {"node_modules", ".next", ".git", "dist", "build", ".turbo"}


def _read_tree(root: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.lower().endswith(_SCANNABLE_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            try:
                with open(full, encoding="utf-8") as fh:
                    files[os.path.relpath(full, root)] = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
    return files


def _main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "."
    report = scan(_read_tree(root))
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = ["DEFECT_CLASSES", "Defect", "DefectReport", "scan"]
