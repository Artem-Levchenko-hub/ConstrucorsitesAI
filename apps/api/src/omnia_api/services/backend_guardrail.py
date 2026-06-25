"""Backend-authoring guardrail (G003) — let the writer author real server logic,
but make it impossible to bypass the access model.

The entity engine's safety property is "the model never writes the query, so it
can't forget owner/membership scoping." The old rule enforced that by BANNING all
backend authoring (`SYSTEM_PROMPT`: never write server/db code) — which also
capped every app at CRUD-over-entities and is the #1 reason generated apps are
prototypes, not real software.

This lifts the ban and replaces it with a GUARDRAIL. The writer MAY author server
actions and custom route handlers (orchestration, computed results, multi-entity
workflows). The one thing it may NEVER do is reach the database RAW — importing
`@/lib/db` / `drizzle-orm` / `pg` outside the fixed engine is the only way to
bypass auth + ownership + membership, so that is the line. Custom server code must
go THROUGH the engine/SDK, which still enforces the access model on every touch.

This scanner enforces the line statically over the writer's generated files. The
pure logic (:func:`scan_backend_safety` / :func:`summarize`) is unit-tested
without a container. Gated by ``Settings.use_backend_guardrail``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Path prefixes that ARE the fixed engine — the only code allowed to touch the
# database directly. Everything else is writer-authored and must go through the
# engine/SDK. Matched as a prefix on the POSIX-style repo-relative path.
_ENGINE_PREFIXES: tuple[str, ...] = (
    "src/lib/db/",
    "src/lib/entities/",
    "src/lib/auth",
    "src/lib/session",
    "src/lib/integrations/",
    "src/app/api/entities/",  # fixed CRUD routes (template-owned)
    "src/app/api/auth/",      # fixed auth routes (template-owned)
    "src/app/api/users/",     # fixed users directory (template-owned)
    "scripts/",
)

# Raw data-access patterns. Importing any of these is the ONLY way to run a query
# the engine didn't scope — so outside the engine they are forbidden.
_RAW_DB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"""from\s+['"]@/lib/db['"]"""), "raw DB client import (@/lib/db)"),
    (re.compile(r"""from\s+['"]drizzle-orm['"]"""), "drizzle-orm import (raw queries)"),
    (re.compile(r"""from\s+['"]drizzle-orm/"""), "drizzle-orm submodule import"),
    (re.compile(r"""from\s+['"]pg['"]"""), "node-postgres (pg) import"),
    (re.compile(r"""from\s+['"]postgres['"]"""), "postgres.js import"),
    (re.compile(r"""\bnew\s+Pool\s*\("""), "raw pg Pool construction"),
)

# Only server-executed files can touch the DB; a guardrail violation only matters
# in code that runs on the server.
_SERVER_FILE = re.compile(r"\.(ts|tsx|mts|cts)$")


@dataclass
class Violation:
    path: str
    rule: str
    detail: str = ""


@dataclass
class GuardrailVerdict:
    safe: bool
    violations: list[Violation] = field(default_factory=list)
    summary: str = ""


def _is_engine_path(path: str) -> bool:
    p = path.replace("\\", "/").lstrip("./")
    return any(p.startswith(prefix) for prefix in _ENGINE_PREFIXES)


def scan_backend_safety(files: dict[str, str]) -> list[Violation]:
    """Flag every writer-authored server file that reaches the database raw.

    `files` maps repo-relative path -> content (the writer's generated/edited
    files). Engine-owned files are skipped (they ARE the safe data layer)."""
    violations: list[Violation] = []
    for path, content in files.items():
        if not _SERVER_FILE.search(path):
            continue
        if _is_engine_path(path):
            continue
        for pattern, rule in _RAW_DB_PATTERNS:
            if pattern.search(content):
                violations.append(
                    Violation(
                        path=path.replace("\\", "/"),
                        rule=rule,
                        detail="custom server code must use the engine/SDK, not the DB directly",
                    )
                )
    return violations


def summarize(violations: list[Violation]) -> GuardrailVerdict:
    """Aggregate violations into a verdict. Pure — unit-testable. Safe iff there
    are zero raw-DB escapes in writer code."""
    if not violations:
        return GuardrailVerdict(safe=True, violations=[], summary="backend guardrail OK — no raw-DB escapes")
    where = ", ".join(sorted({v.path for v in violations}))
    return GuardrailVerdict(
        safe=False,
        violations=violations,
        summary=f"backend guardrail FAILED — raw DB access outside the engine in: {where}",
    )


def check_backend(files: dict[str, str]) -> GuardrailVerdict:
    """Convenience: scan + summarize in one call (what the gate calls)."""
    return summarize(scan_backend_safety(files))
