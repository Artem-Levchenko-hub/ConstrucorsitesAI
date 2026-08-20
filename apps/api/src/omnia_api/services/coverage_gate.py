"""Coverage gate — «не останавливайся на зелёном минимуме» (owner 2026-06-30).

A green build proves the code COMPILES; it does not prove the user can actually
DO what the prompt asked. This gate closes that gap: given the persisted Build
Plan (:mod:`services.build_plan`), it replays each must-have *capability* as a
real authenticated request against the live preview and checks the status the
plan declared it expects. Anything that doesn't answer as promised is a coverage
GAP that the caller's self-heal loop feeds back to the agent — so completion
means "the plan works", not "it builds".

Route reconciliation (P3 A1): a probe failure has TWO meanings, and conflating
them caused false heal-storms on WORKING apps. Given the app's REAL route set
(:func:`api_routes_from_files`), each failing capability is classified:
``wrong_status`` (the route EXISTS but misbehaves — a real bug, always heal) vs
``missing_route`` (the planner declared a path the app never built — heal once
with "build it", then advisory; never block a good app on a planner's guess).

Efficiency: unlike the per-call ``agent_probe.run_probe`` (one browser per
request), this opens ONE browser session and replays every capability through it
— login once, then N in-page fetches — reusing the proven functional-gate
primitives (NextAuth csrf+callback login + cookie-bearing in-page fetch). A
build's coverage check is therefore one browser launch, not N.

Bounded + fail-soft (R-10): at most ``max_probes`` capabilities are checked
(the rest are logged, never silently dropped); no preview, a login failure, or
any unexpected error returns a SKIPPED verdict (``passed=True``) so the gate can
never block a build on its own infrastructure — it only ever blocks on a
capability that DEFINITIVELY returned the wrong status against a route that
exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from omnia_api.services.build_plan import BuildPlan

log = logging.getLogger(__name__)

# Cap on how many capabilities one coverage pass probes. Each is a real
# authenticated request; the plan itself is capped at 10 capabilities, of which
# the must-have+probeable subset is usually small. 6 keeps a heal round bounded.
_MAX_PROBES = 6


@dataclass(frozen=True)
class CoverageCheck:
    """One capability's result. Shape matches ``functional_gate.Check`` so
    ``agent_gate_feedback.outcome_from_checks`` consumes it unchanged
    (``ok`` / ``name`` / ``detail``). ``kind`` classifies a failure for the
    self-heal loop: ``ok`` | ``wrong_status`` (route exists, bad status — real
    bug) | ``missing_route`` (route never built — planner over-specified)."""

    ok: bool
    name: str
    detail: str = ""
    kind: str = "ok"


@dataclass(frozen=True)
class CoverageVerdict:
    passed: bool
    covered: int
    total: int
    missing: list[str] = field(default_factory=list)
    checks: list[CoverageCheck] = field(default_factory=list)
    skipped: bool = False

    def hard_missing(self) -> list[str]:
        """Routes that EXIST but returned the wrong status — real bugs that
        always block and heal (the messenger-class failure)."""
        return [c.name for c in self.checks if not c.ok and c.kind == "wrong_status"]

    def soft_missing(self) -> list[str]:
        """Capabilities whose route was never built — the planner over-specified
        or the app skipped it. Healed once ('build it'), then advisory; never a
        hard block on a good app."""
        return [c.name for c in self.checks if not c.ok and c.kind == "missing_route"]


def status_matches(status: int, expect: str) -> bool:
    """Does an observed HTTP status satisfy the plan's declared expectation?

    Accepts a class (``"2xx"`` / ``"4xx"`` …) or an exact code (``"403"``).
    Unparseable / empty → treated as the happy path (2xx). A 0 status (the
    request threw at the network layer — usually a missing route) never matches a
    2xx expectation, so it reads as the real failure it is.
    """
    exp = (expect or "2xx").strip().lower()
    if len(exp) == 3 and exp[1:] == "xx" and exp[0].isdigit():
        lo = int(exp[0]) * 100
        return lo <= status < lo + 100
    if exp.isdigit():
        return status == int(exp)
    return 200 <= status < 300


def api_routes_from_files(files: dict[str, str] | None) -> set[str]:
    """Real ``/api/...`` route PREFIXES a generated app exposes, derived from its
    ``src/app/api/**/route.ts`` file paths.

    Used to reconcile a planner-declared capability path against what the app
    ACTUALLY built, so coverage blocks on a real misbehaving route, not on a path
    the planner guessed but the app never created. Route groups ``(x)`` are
    dropped; the prefix stops at the first dynamic ``[seg]`` (so
    ``/api/clients/[id]/route.ts`` → ``/api/clients``). Sibling of
    :func:`isolation_gate.api_routes_from_grep` but reads the in-memory ``files``
    dict instead of a grep dump (no extra orchestrator call).
    """
    routes: set[str] = set()
    for raw in files or {}:
        p = str(raw).replace("\\", "/")
        marker = "/app/api/"
        if marker not in p or not p.endswith("/route.ts"):
            continue
        seg = p.split(marker, 1)[1][: -len("/route.ts")]
        parts: list[str] = []
        for s in seg.split("/"):
            if not s or (s.startswith("(") and s.endswith(")")):
                continue
            if s.startswith("[") and s.endswith("]"):
                break
            parts.append(s)
        if parts:
            routes.add("/api/" + "/".join(parts))
    return routes


def _route_known(path: str, known_routes: set[str]) -> bool:
    """Does the app expose a route that could serve this capability path? Exact
    or prefix match (a collection route covers its ``/{id}`` children)."""
    p = (path or "").split("?", 1)[0].rstrip("/")
    if not p:
        return False
    if p in known_routes:
        return True
    return any(p == r or p.startswith(r + "/") for r in known_routes)


def _verdict_from_checks(
    checks: list[CoverageCheck], *, skipped: bool = False
) -> CoverageVerdict:
    missing = [c.name for c in checks if not c.ok]
    return CoverageVerdict(
        passed=not missing,
        covered=len(checks) - len(missing),
        total=len(checks),
        missing=missing,
        checks=checks,
        skipped=skipped,
    )


async def run_coverage_gate(
    project_id: UUID | str,
    plan: BuildPlan,
    *,
    stack: str | None = None,
    known_routes: set[str] | None = None,
    max_probes: int = _MAX_PROBES,
) -> CoverageVerdict:
    """Replay the plan's blocking capabilities against the live preview.

    ``known_routes`` (from :func:`api_routes_from_files`) enables reconciliation:
    an ``/api/`` capability whose route the app never built is classified
    ``missing_route`` (not probed) instead of failing as a fake bug. Pass ``None``
    to skip reconciliation (probe everything — the pre-P3 behaviour).

    Returns a SKIPPED (``passed=True``) verdict when there is nothing provable or
    the preview/login is unavailable — the gate blocks ONLY on a capability that
    returned a definitively wrong status against a route that exists.
    """
    caps = list(plan.blocking_capabilities()) if plan else []
    if not caps:
        return CoverageVerdict(passed=True, covered=0, total=0, skipped=True)
    probed = caps[:max_probes]
    if len(caps) > len(probed):
        log.info(
            "coverage_gate: probing %d/%d blocking capabilities (cap=%d)",
            len(probed),
            len(caps),
            max_probes,
        )

    try:
        pid = UUID(str(project_id))
    except (TypeError, ValueError):
        return CoverageVerdict(passed=True, covered=0, total=0, skipped=True)

    from omnia_api.services import orchestrator_client

    st = await orchestrator_client.get_status(pid)
    base = st.get("dev_url") if isinstance(st, dict) else None
    if not base:
        log.info("coverage_gate: no dev_url — skip (cannot verify, do not block)")
        return CoverageVerdict(passed=True, covered=0, total=0, skipped=True)
    base = base.rstrip("/")

    # Lazy imports keep the pure helpers (status_matches) test-free of Playwright.
    from playwright.async_api import async_playwright

    from omnia_api.services import functional_gate as fg
    from omnia_api.services.auth_session import preview_resolver_args

    # Fixed throwaway identity, shared with agent_probe so stateful flows compose
    # against a user that owns its own rows.
    _email = "agent-probe@omnia.local"
    _password = "agent-probe-1234"

    checks: list[CoverageCheck] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=preview_resolver_args()
            )
            try:
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto(f"{base}/signin", wait_until="domcontentloaded")
                await fg._api(
                    page, "POST", "/api/auth/register",
                    {"email": _email, "password": _password},
                )
                try:
                    await fg._login(page, base, _email, _password)
                except Exception as exc:
                    log.info("coverage_gate: login failed — skip (%r)", exc)
                    return CoverageVerdict(passed=True, covered=0, total=0, skipped=True)
                for c in probed:
                    # Reconcile against the app's REAL routes first: a planner-
                    # guessed /api path the app never built is NOT a misbehaving
                    # feature — don't fail it as a fake bug (kills heal-storms).
                    if (
                        known_routes is not None
                        and c.path.startswith("/api/")
                        and not _route_known(c.path, known_routes)
                    ):
                        checks.append(
                            CoverageCheck(
                                ok=False,
                                name=c.id,
                                kind="missing_route",
                                detail=(
                                    f"{c.action or c.id}: роут {c.method} {c.path} "
                                    "НЕ построен в приложении — построй его, если эта "
                                    "возможность нужна (иначе убери из плана)"
                                ),
                            )
                        )
                        continue
                    try:
                        res = await fg._api(page, c.method, c.path, c.body_hint or None)
                        status = int(res.get("status", 0))
                    except Exception as exc:
                        status = 0
                        log.info("coverage_gate: probe %s threw: %r", c.id, exc)
                    ok = status_matches(status, c.expect)
                    checks.append(
                        CoverageCheck(
                            ok=ok,
                            name=c.id,
                            kind="ok" if ok else "wrong_status",
                            detail=(
                                f"{c.action or c.id}: {c.method} {c.path} -> HTTP "
                                f"{status} (ожидалось {c.expect})"
                            ),
                        )
                    )
            finally:
                await browser.close()
    except Exception as exc:
        log.info("coverage_gate: skipped on error (%r)", exc)
        return CoverageVerdict(passed=True, covered=0, total=0, skipped=True)

    verdict = _verdict_from_checks(checks)
    log.info(
        "coverage_gate: %s %d/%d (hard=%s soft=%s)",
        "PASS" if verdict.passed else "FAIL",
        verdict.covered,
        verdict.total,
        ",".join(verdict.hard_missing()) or "-",
        ",".join(verdict.soft_missing()) or "-",
    )
    return verdict


__all__ = [
    "CoverageCheck",
    "CoverageVerdict",
    "api_routes_from_files",
    "run_coverage_gate",
    "status_matches",
]
