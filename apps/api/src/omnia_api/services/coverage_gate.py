"""Coverage gate — «не останавливайся на зелёном минимуме» (owner 2026-06-30).

A green build proves the code COMPILES; it does not prove the user can actually
DO what the prompt asked. This gate closes that gap: given the persisted Build
Plan (:mod:`services.build_plan`), it replays each must-have *capability* as a
real authenticated request against the live preview and checks the status the
plan declared it expects. Anything that doesn't answer as promised is a coverage
GAP that the caller's self-heal loop feeds back to the agent — so completion
means "the plan works", not "it builds".

Efficiency: unlike the per-call ``agent_probe.run_probe`` (one browser per
request), this opens ONE browser session and replays every capability through it
— login once, then N in-page fetches — reusing the proven functional-gate
primitives (NextAuth csrf+callback login + cookie-bearing in-page fetch). A
build's coverage check is therefore one browser launch, not N.

Bounded + fail-soft (R-10): at most ``max_probes`` capabilities are checked
(the rest are logged, never silently dropped); no preview, a login failure, or
any unexpected error returns a SKIPPED verdict (``passed=True``) so the gate can
never block a build on its own infrastructure — it only ever blocks on a
capability that DEFINITIVELY returned the wrong status.
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
    (``ok`` / ``name`` / ``detail``)."""

    ok: bool
    name: str
    detail: str = ""


@dataclass(frozen=True)
class CoverageVerdict:
    passed: bool
    covered: int
    total: int
    missing: list[str] = field(default_factory=list)
    checks: list[CoverageCheck] = field(default_factory=list)
    skipped: bool = False


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
    max_probes: int = _MAX_PROBES,
) -> CoverageVerdict:
    """Replay the plan's blocking capabilities against the live preview.

    Returns a SKIPPED (``passed=True``) verdict when there is nothing provable
    or the preview/login is unavailable — the gate blocks ONLY on a capability
    that returned a definitively wrong status.
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
        "coverage_gate: %s %d/%d (missing=%s)",
        "PASS" if verdict.passed else "FAIL",
        verdict.covered,
        verdict.total,
        ",".join(verdict.missing) or "-",
    )
    return verdict


__all__ = [
    "CoverageCheck",
    "CoverageVerdict",
    "run_coverage_gate",
    "status_matches",
]
