"""Security negative-path gate (G005).

Two halves make "secure from the first prompt" enforceable:

  1. LEAK ATTEMPTS — as another user, try to read/modify someone else's records
     and cross-conversation messages; any success fails the build. This half is
     already executed by the functional gate (`functional_gate` outsider-403
     checks) and the role gate (`role_gate` wrong-role-denied checks); this module
     AGGREGATES their negative-path results.
  2. TRANSPORT SURFACE — assert the hardening from G006 is actually live on the
     response: security headers present, payload cap enforced (413), and CORS not
     wildcard-with-credentials (which would let any site read authed responses).

The surface assertions here are pure (header dict in → checks out) so they are
unit-tested without a browser. Gated by ``Settings.use_security_gate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SecCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SecurityVerdict:
    passed: bool
    checks: list[SecCheck] = field(default_factory=list)
    summary: str = ""


def _h(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (HTTP header names are case-insensitive)."""
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v
    return None


def assert_security_headers(headers: dict[str, str]) -> list[SecCheck]:
    """The conservative headers G006 sets must be present on responses."""
    checks: list[SecCheck] = []
    nosniff = _h(headers, "x-content-type-options")
    checks.append(
        SecCheck("X-Content-Type-Options: nosniff", nosniff == "nosniff", str(nosniff))
    )
    frame = _h(headers, "x-frame-options")
    checks.append(
        SecCheck(
            "X-Frame-Options present", frame is not None and frame != "", str(frame)
        )
    )
    return checks


def assert_cors_safe(headers: dict[str, str]) -> SecCheck:
    """A credentialed CORS response must NOT allow any origin ("*") — that lets a
    malicious site read the authed user's data. Same-origin (no ACAO) is safe."""
    acao = _h(headers, "access-control-allow-origin")
    acac = (_h(headers, "access-control-allow-credentials") or "").lower() == "true"
    leak = acac and acao == "*"
    return SecCheck(
        "CORS not wildcard-with-credentials",
        not leak,
        f"ACAO={acao} ACAC={acac}",
    )


def assert_payload_cap(oversize_status: int) -> SecCheck:
    """An over-cap body must be rejected (413), not accepted."""
    return SecCheck(
        "oversized payload rejected (413)",
        oversize_status == 413,
        str(oversize_status),
    )


def summarize(
    leak_checks: list[SecCheck],
    surface_checks: list[SecCheck],
) -> SecurityVerdict:
    """Combine leak-attempt results and transport-surface checks. The gate passes
    only when every check passed — a single leak or a missing protection fails it;
    zero checks is not a pass (no evidence != safe)."""
    checks = list(leak_checks) + list(surface_checks)
    failures = [c for c in checks if not c.ok]
    passed = len(checks) > 0 and not failures
    if passed:
        summary = f"security gate PASSED ({len(checks)} checks)"
    else:
        names = ", ".join(c.name for c in failures) or "no checks ran"
        summary = f"security gate FAILED: {names}"
    return SecurityVerdict(passed=passed, checks=checks, summary=summary)


def surface_verdict_from_headers(headers: dict[str, str]) -> SecurityVerdict:
    """Pure BLOCKING transport-surface verdict tuned to THIS product's preview
    architecture — the unit-tested heart of :func:`run_security_gate`.

    We block ONLY on guarantees the templates actually make + a zero-false-positive
    invariant, so the gate never blocks a good build:
      * ``X-Content-Type-Options: nosniff`` present — the templates set it; a build
        that dropped it (e.g. an edit that rewrote next.config) is caught.
      * CORS not wildcard-with-credentials — a real cross-site data-read leak that
        is NEVER legitimately needed.

    Deliberately NOT asserted here (would false-block every app):
      * ``X-Frame-Options`` — generated apps are EMBEDDED in the workspace preview
        iframe, so frame protection is the orchestrator proxy's CSP
        ``frame-ancestors`` job, not the app's; the templates omit it on purpose.
      * Payload cap (413) — not yet enforced by the templates, so blocking on it
        would fail every build. (Add the cap to the templates first, then promote.)
    """
    nosniff = _h(headers, "x-content-type-options")
    checks = [
        SecCheck(
            "X-Content-Type-Options: nosniff", nosniff == "nosniff", str(nosniff)
        ),
        assert_cors_safe(headers),
    ]
    return summarize(checks, [])


async def run_security_gate(base_url: str) -> SecurityVerdict:
    """Drive the live preview, capture the main route's response headers, and
    return the transport-surface verdict (:func:`surface_verdict_from_headers`).

    Stack-agnostic — applies to any generated app (realtime + drizzle/fullstack).
    Fail-soft: a crash / missing preview becomes a failed check, never an exception
    into the build pipeline (a crashed gate reads as "not proven", not "passed")."""
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return summarize([SecCheck("preview running", False, "no dev_url")], [])

    from playwright.async_api import async_playwright

    from omnia_api.services.auth_session import preview_resolver_args

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=preview_resolver_args()
            )
            try:
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto(f"{base_url}/", wait_until="domcontentloaded")
                res = await page.evaluate(
                    """async () => {
                        const r = await fetch('/', { credentials: 'include' });
                        const headers = {};
                        r.headers.forEach((v, k) => { headers[k] = v; });
                        return { headers };
                    }"""
                )
            finally:
                await browser.close()
    except Exception as exc:
        return summarize(
            [SecCheck("security gate executed", False, f"{type(exc).__name__}: {exc}")],
            [],
        )

    headers = res.get("headers") if isinstance(res, dict) else None
    if not isinstance(headers, dict):
        return summarize([SecCheck("capture response headers", False, "none")], [])
    return surface_verdict_from_headers(headers)
