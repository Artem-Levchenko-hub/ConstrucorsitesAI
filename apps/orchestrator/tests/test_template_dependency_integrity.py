"""Guard: every npm package a template's `src/` imports MUST be declared in its
package.json. This catches "kit file copied, dependency forgotten" drift — the
exact failure that shipped `src/components/ui/sonner.tsx` into the realtime +
drizzle templates without `"sonner"` in dependencies, so `tsc --noEmit` died
with TS2307 and EVERY build on those stacks aborted ("Сборка прервана") before
anything rendered (live on prod project 360fa2aa, 2026-06-27).

The dev image bakes node_modules from package.json, so a bare import with no
declared dep is unresolvable at typecheck time — a deterministic, 100%-repro
build break. A unit test is the right altitude: it fails in CI/local before the
image is ever built, instead of surfacing as a dead app for the user. [R-09]
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# templates/ sits next to the orchestrator package dir; this file is
# apps/orchestrator/tests/, so templates is ../templates.
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

# `from "x"` / `import "x"` / `require("x")` / dynamic `import("x")`.
_IMPORT_RE = re.compile(r"""(?:from|import|require\()\s*['"]([^'"]+)['"]""")

# A real npm package specifier: optional @scope/, then name; subpaths ignored.
# Filters out regex/parse artifacts (commas, whitespace) so the guard never
# false-positives on multiline import lists.
_PKG_NAME_RE = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")

# Node builtins + path aliases that are never package.json deps.
_NODE_BUILTINS = frozenset(
    {
        "fs", "path", "crypto", "os", "http", "https", "stream", "util",
        "events", "url", "child_process", "zlib", "buffer", "net", "tls",
        "dns", "assert", "querystring", "readline", "process", "perf_hooks",
        "timers", "worker_threads", "module",
    }
)


def _package_of(spec: str) -> str | None:
    """Reduce an import specifier to the npm package that must be declared.

    Returns None for things that are never a package.json dependency: relative
    paths, the `@/`/`~/` tsconfig aliases, `node:` builtins, bare builtins, and
    anything that isn't a syntactically valid package name (parse artifacts).
    `@scope/name/sub` → `@scope/name`; `name/sub` → `name`.
    """
    if spec.startswith((".", "/", "@/", "~/", "node:")):
        return None
    parts = spec.split("/")
    name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
    if name in _NODE_BUILTINS:
        return None
    if not _PKG_NAME_RE.match(name):
        return None
    return name


def _next_templates() -> list[Path]:
    """Templates that have both a package.json and a TS/TSX src/ tree."""
    out: list[Path] = []
    for d in sorted(_TEMPLATES_DIR.iterdir()):
        if (d / "package.json").exists() and (d / "src").is_dir():
            out.append(d)
    return out


@pytest.mark.parametrize("template", _next_templates(), ids=lambda p: p.name)
def test_every_src_import_is_declared(template: Path) -> None:
    data = json.loads((template / "package.json").read_text(encoding="utf-8"))
    declared = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))

    src = template / "src"
    missing: dict[str, str] = {}
    # Only the template's OWN source — never an installed node_modules/*/src tree
    # (entities carries a local install; those files are not ours to vet).
    for f in (*src.rglob("*.ts"), *src.rglob("*.tsx")):
        if "node_modules" in f.parts:
            continue
        for spec in _IMPORT_RE.findall(f.read_text(encoding="utf-8", errors="ignore")):
            pkg = _package_of(spec)
            if pkg and pkg not in declared:
                missing.setdefault(pkg, str(f.relative_to(template)))

    assert not missing, (
        f"{template.name}: src imports packages not in package.json "
        f"(tsc --noEmit will fail, build aborts): "
        + ", ".join(f"{pkg} (e.g. {where})" for pkg, where in sorted(missing.items()))
    )


def test_guard_sees_some_templates() -> None:
    # Self-check: the parametrize discovered real templates (a glob typo that
    # silently matched nothing would make the guard above vacuously pass).
    assert len(_next_templates()) >= 3
