"""Unit tests for the build-time dependency doctor (pure planner).

Covers the safety-critical contract: install ONLY allowlisted/trusted-scope
packages that are imported-but-undeclared; never install a hallucinated name;
never crash on malformed package.json. The sonner drift (the live prod break on
2026-06-27) is the canonical case.
"""

from __future__ import annotations

import json

from omnia_orchestrator.services import dep_doctor

_PJ = json.dumps(
    {
        "dependencies": {"next": "^15", "react": "^19", "clsx": "^2"},
        "devDependencies": {"typescript": "^5"},
    }
)


def test_package_of_reduces_and_filters() -> None:
    assert dep_doctor.package_of("sonner") == "sonner"
    assert dep_doctor.package_of("date-fns/format") == "date-fns"
    assert dep_doctor.package_of("@radix-ui/react-dialog") == "@radix-ui/react-dialog"
    assert dep_doctor.package_of("@radix-ui/react-dialog/foo") == "@radix-ui/react-dialog"
    # Never a dependency:
    assert dep_doctor.package_of("./local") is None
    assert dep_doctor.package_of("@/lib/utils") is None
    assert dep_doctor.package_of("~/lib/x") is None
    assert dep_doctor.package_of("node:fs") is None
    assert dep_doctor.package_of("fs") is None
    # Parse artifact (multiline import list garbage) → rejected by the name regex.
    assert dep_doctor.package_of(",\n  ") is None


def test_is_allowed_allowlist_and_trusted_scope() -> None:
    assert dep_doctor.is_allowed("sonner")                       # explicit allowlist
    assert dep_doctor.is_allowed("@radix-ui/react-anything")     # trusted scope
    assert dep_doctor.is_allowed("@hookform/resolvers")          # trusted scope
    assert dep_doctor.is_allowed("@tanstack/react-query")        # trusted scope
    assert not dep_doctor.is_allowed("reqct")                    # hallucination
    assert not dep_doctor.is_allowed("totally-made-up-pkg")
    assert not dep_doctor.is_allowed("@evil/payload")            # untrusted scope


def test_plan_installs_heals_the_sonner_drift() -> None:
    # The exact live failure: sonner.tsx imports "sonner", package.json lacks it.
    src = 'import { Toaster as Sonner } from "sonner";\nimport { cn } from "@/lib/utils";'
    assert dep_doctor.plan_installs(_PJ, src) == ["sonner"]


def test_plan_installs_skips_already_declared() -> None:
    src = 'import Link from "next/link";\nimport { clsx } from "clsx";'
    # next + clsx are declared → nothing to add.
    assert dep_doctor.plan_installs(_PJ, src) == []


def test_plan_installs_never_installs_hallucinated_packages() -> None:
    # A model typo / hallucinated import must NOT trigger an install — it falls
    # through to the normal tsc error. THIS is the supply-chain guard.
    src = 'import x from "reqct";\nimport y from "leftpad-evil-typo";'
    assert dep_doctor.plan_installs(_PJ, src) == []


def test_plan_installs_allows_trusted_scope_not_in_allowlist() -> None:
    src = 'import * as Accordion from "@radix-ui/react-accordion";'
    assert dep_doctor.plan_installs(_PJ, src) == ["@radix-ui/react-accordion"]


def test_plan_installs_sorted_and_deduped() -> None:
    src = (
        'import "sonner";\nimport "sonner";\n'
        'import { format } from "date-fns";\n'
        'import { z } from "zod";'
    )
    assert dep_doctor.plan_installs(_PJ, src) == ["date-fns", "sonner", "zod"]


def test_declared_deps_tolerates_malformed_json() -> None:
    # A broken package.json must not crash the build path (fail-soft → {}).
    assert dep_doctor.declared_deps("{ not valid json ") == set()
    assert dep_doctor.declared_deps("") == set()
    # Plan still computes (treats nothing as declared) and stays allowlist-bound.
    assert dep_doctor.plan_installs("{ broken", 'import "sonner";') == ["sonner"]


def test_imported_packages_captures_dynamic_imports() -> None:
    # Dynamic `import("pkg")` / `await import("pkg")` of a BARE package must be
    # seen (a real one exists: nextjs-realtime hub.ts does `await import("ioredis")`).
    src = (
        'const m = import("date-fns");\n'
        'await import("recharts");\n'
        'const d = await import("./local");\n'  # relative → dropped
        'import("node:crypto");'                # builtin → dropped
    )
    assert dep_doctor.imported_packages(src) == {"date-fns", "recharts"}


def test_imported_packages_ignores_local_and_builtins() -> None:
    src = (
        'import { db } from "./db";\n'
        'import fs from "fs";\n'
        'import path from "node:path";\n'
        'import { cn } from "@/lib/utils";\n'
        'import { toast } from "sonner";'
    )
    assert dep_doctor.imported_packages(src) == {"sonner"}
