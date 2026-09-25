"""Build-time dependency doctor — pure planner (no I/O).

The dev image bakes ``node_modules`` from the template's ``package.json``. When
generated code (or a template-kit drift) imports a package that isn't declared,
``tsc`` dies with ``TS2307: Cannot find module 'X'`` and the WHOLE build aborts
before anything renders — exactly the class of error the agent's source-only
edits cannot fix (it can rewrite ``.tsx`` files, but a baked ``node_modules`` is
not a source file). This module decides which packages to ``pnpm add`` BEFORE
the typecheck so that class of failure self-heals.

Safety is the whole design: a package is installed ONLY when its name is both
syntactically valid AND on a curated allowlist (or under a trusted scope). A
hallucinated import name (``import x from "reqct"``) can therefore never trigger
an install — it falls through to the normal ``tsc`` error, same as today. This
is the typosquat / supply-chain guard: we never run ``pnpm add`` on an arbitrary
string the model emitted. [R-10 fail-soft, R-01 deep module]

Pure by construction: the caller (orchestrator runtime endpoint) does the
container I/O — read ``package.json``, grep ``src`` imports, run ``pnpm add`` —
and feeds the text in here. That keeps this module trivially unit-testable.
"""

from __future__ import annotations

import json
import re

# `from "x"` / `import "x"` / dynamic `import("x")` / `require("x")`. The
# `import\(` alternative precedes bare `import` so a dynamic import resolves to
# it directly (no reliance on backtracking) while a side-effect `import "x"`
# still matches the bare branch.
_IMPORT_RE = re.compile(r"""(?:from|import\(|import|require\()\s*['"]([^'"]+)['"]""")

# A syntactically valid npm package specifier: optional @scope/, then name.
# Anything failing this (parse artifacts, odd chars) is dropped — never installed.
_PKG_NAME_RE = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")

# Node builtins + path aliases that are never a package.json dependency.
_NODE_BUILTINS = frozenset(
    {
        "fs", "path", "crypto", "os", "http", "https", "stream", "util",
        "events", "url", "child_process", "zlib", "buffer", "net", "tls",
        "dns", "assert", "querystring", "readline", "process", "perf_hooks",
        "timers", "worker_threads", "module",
    }
)

# Trusted scopes: any package under these well-known orgs is safe to auto-install.
# Covers the entire shadcn/ui radix surface + form/query ecosystems the generator
# legitimately reaches for, without enumerating every component package.
_TRUSTED_SCOPES = ("@radix-ui/", "@hookform/", "@tanstack/")

# Curated allowlist = the union of every template's package.json deps (so any kit
# component drift is always fixable) + the common generation libraries the model
# reaches for. A package NOT here and NOT under a trusted scope is deliberately
# left as a tsc error. Keep additions deliberate — this is the install boundary.
_ALLOWLIST = frozenset(
    {
        # ── template/kit union (apps/orchestrator/templates/*/package.json) ──
        "@auth/drizzle-adapter", "@radix-ui/react-avatar", "@radix-ui/react-checkbox",
        "@radix-ui/react-dialog", "@radix-ui/react-dropdown-menu", "@radix-ui/react-label",
        "@radix-ui/react-select", "@radix-ui/react-separator", "@radix-ui/react-slot",
        "@radix-ui/react-tabs", "@radix-ui/react-tooltip", "@tailwindcss/postcss",
        "@tailwindcss/vite", "@vitejs/plugin-react", "bcryptjs",
        "class-variance-authority", "clsx", "drizzle-kit", "drizzle-orm", "ioredis",
        "lucide-react", "minio", "next", "next-auth", "pg", "postcss", "react",
        "react-dom", "react-router-dom", "sonner", "tailwind-merge", "tailwindcss",
        "tw-animate-css", "typescript", "vite", "zod",
        # ── common generation libraries (well-known, vetted) ──
        "date-fns", "recharts", "framer-motion", "motion", "react-hook-form",
        "zustand", "swr", "cmdk", "vaul", "embla-carousel-react", "input-otp",
        "react-day-picker", "next-themes", "zod-form-data", "react-icons",
        "@auth/core", "drizzle-zod", "nanoid", "uuid", "slugify",
    }
)


def package_of(spec: str) -> str | None:
    """Reduce an import specifier to the npm package that must be declared.

    Returns None for things that are never a package.json dependency: relative
    paths, the ``@/`` / ``~/`` tsconfig aliases, ``node:`` builtins, bare
    builtins, and anything that isn't a syntactically valid package name.
    ``@scope/name/sub`` → ``@scope/name``; ``name/sub`` → ``name``.
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


def is_allowed(pkg: str) -> bool:
    """Whether this package may be auto-installed (allowlist or trusted scope)."""
    return pkg in _ALLOWLIST or pkg.startswith(_TRUSTED_SCOPES)


def declared_deps(package_json_text: str) -> set[str]:
    """Every declared dependency (runtime + dev). Tolerant of malformed JSON —
    a broken package.json must not crash the build path, it just means we treat
    nothing as declared (fail-soft; tsc still runs and reports the real error)."""
    try:
        data = json.loads(package_json_text)
    except (ValueError, TypeError):
        return set()
    out: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            out.update(section)
    return out


def imported_packages(src_text: str) -> set[str]:
    """The set of external npm packages referenced by import/require statements
    in the given source text (e.g. the concatenated grep output of src/)."""
    pkgs: set[str] = set()
    for spec in _IMPORT_RE.findall(src_text):
        pkg = package_of(spec)
        if pkg is not None:
            pkgs.add(pkg)
    return pkgs


def plan_installs(package_json_text: str, src_text: str) -> list[str]:
    """The sorted list of packages to ``pnpm add``: imported by src, NOT already
    declared, AND permitted by the allowlist / trusted scopes. Empty when there
    is nothing safe to add (the common case once a build is healthy)."""
    declared = declared_deps(package_json_text)
    missing = {
        pkg
        for pkg in imported_packages(src_text)
        if pkg not in declared and is_allowed(pkg)
    }
    return sorted(missing)
