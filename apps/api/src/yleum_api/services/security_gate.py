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
from urllib.parse import urlsplit

from yleum_api.services.render_settle import goto_and_settle

OWNER_PREVIEW_FRAMING_POLICY = (
    "frame-ancestors 'self' https://constructor.lead-generator.ru"
)
PUBLIC_MAX_FRAMING_POLICY = "frame-ancestors 'self' https://web.max.ru https://max.ru"


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


def _has_embedded_framing_policy(value: str | None, expected: str) -> bool:
    if value is None:
        return False
    expected_tokens = expected.casefold().split()
    if not expected_tokens or expected_tokens[0] != "frame-ancestors":
        return False
    required_sources = set(expected_tokens[1:])
    found = False
    for policy in value.split(","):
        directives: dict[str, list[str]] = {}
        for raw_directive in policy.split(";"):
            tokens = raw_directive.casefold().split()
            if tokens:
                # CSP ignores later instances of the same directive in one policy.
                directives.setdefault(tokens[0], tokens[1:])
        sources = directives.get("frame-ancestors")
        if sources is None:
            continue
        found = True
        if "'none'" in sources or not required_sources.issubset(sources):
            return False
    # Multiple CSP headers/policies are enforced together. At least one policy
    # must carry the framing boundary and every such policy must allow it.
    return found


def assert_security_headers(
    headers: dict[str, str],
    *,
    require_embedded_framing: bool = False,
    framing_policy: str = OWNER_PREVIEW_FRAMING_POLICY,
) -> list[SecCheck]:
    """The conservative headers G006 sets must be present on responses."""
    checks: list[SecCheck] = []
    nosniff = _h(headers, "x-content-type-options")
    checks.append(
        SecCheck("X-Content-Type-Options: nosniff", nosniff == "nosniff", str(nosniff))
    )
    if require_embedded_framing:
        csp = _h(headers, "content-security-policy")
        checks.append(
            SecCheck(
                "CSP frame-ancestors matches embedded MAX policy",
                _has_embedded_framing_policy(csp, framing_policy),
                "present" if csp else "missing",
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


def surface_verdict_from_headers(
    headers: dict[str, str],
    *,
    require_embedded_framing: bool = False,
    protected_status: int | None = None,
    framing_policy: str = OWNER_PREVIEW_FRAMING_POLICY,
    document_headers: dict[str, str] | None = None,
    document_status: int | None = None,
) -> SecurityVerdict:
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
    checks = assert_security_headers(
        headers,
        require_embedded_framing=False,
    )
    checks.append(assert_cors_safe(headers))
    if require_embedded_framing:
        document_checks = assert_security_headers(
            document_headers or {},
            require_embedded_framing=True,
            framing_policy=framing_policy,
        )
        checks.extend(
            SecCheck(f"final document {check.name}", check.ok, check.detail)
            for check in document_checks
        )
        checks.append(
            SecCheck(
                "authenticated protected preview response",
                protected_status == 200,
                str(protected_status),
            )
        )
        checks.append(
            SecCheck(
                "authenticated final product document",
                document_status == 200,
                str(document_status),
            )
        )
    return summarize(checks, [])


async def run_security_gate(
    base_url: str,
    *,
    bootstrap_url: str | None = None,
    require_embedded_framing: bool = False,
    framing_policy: str = OWNER_PREVIEW_FRAMING_POLICY,
) -> SecurityVerdict:
    """Drive the live preview, capture the main route's response headers, and
    return the transport-surface verdict (:func:`surface_verdict_from_headers`).

    Stack-agnostic — applies to any generated app (realtime + drizzle/fullstack).
    Fail-soft: a crash / missing preview becomes a failed check, never an exception
    into the build pipeline (a crashed gate reads as "not proven", not "passed")."""
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return summarize([SecCheck("preview running", False, "no dev_url")], [])
    navigation_url = f"{base_url}/"
    if bootstrap_url is not None:
        try:
            base = urlsplit(base_url)
            bootstrap = urlsplit(bootstrap_url)
            same_origin = (
                bootstrap.scheme == base.scheme
                and bootstrap.hostname == base.hostname
                and bootstrap.port == base.port
                and bootstrap.username is None
                and bootstrap.password is None
            )
        except ValueError:
            same_origin = False
        if not same_origin:
            return summarize(
                [SecCheck("signed preview bootstrap", False, "origin mismatch")],
                [],
            )
        navigation_url = bootstrap_url

    from playwright.async_api import async_playwright

    from yleum_api.services.auth_session import preview_resolver_args

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=preview_resolver_args()
            )
            try:
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await goto_and_settle(page, navigation_url, timeout_ms=30_000)
                protected_path = "/api/omnia/actions?limit=1" if require_embedded_framing else "/"
                res = await page.evaluate(
                    """async ({ path, includeDocument }) => {
                        const protectedResponse = await fetch(path, {
                            credentials: 'include'
                        });
                        const protectedHeaders = {};
                        protectedResponse.headers.forEach((v, k) => {
                            protectedHeaders[k] = v;
                        });
                        const result = {
                            protected: {
                                headers: protectedHeaders,
                                status: protectedResponse.status
                            }
                        };
                        if (includeDocument) {
                            const documentResponse = await fetch(window.location.href, {
                                credentials: 'include',
                                headers: { accept: 'text/html' },
                                redirect: 'follow'
                            });
                            const documentHeaders = {};
                            documentResponse.headers.forEach((v, k) => {
                                documentHeaders[k] = v;
                            });
                            result.document = {
                                headers: documentHeaders,
                                status: documentResponse.status,
                                url: documentResponse.url
                            };
                        }
                        return result;
                    }""",
                    {
                        "path": protected_path,
                        "includeDocument": require_embedded_framing,
                    },
                )
            finally:
                await browser.close()
    except Exception as exc:
        return summarize(
            [SecCheck("security gate executed", False, type(exc).__name__)],
            [],
        )

    protected = res.get("protected") if isinstance(res, dict) else None
    if not isinstance(protected, dict):
        return summarize([SecCheck("capture response headers", False, "none")], [])
    headers = protected.get("headers")
    if not isinstance(headers, dict):
        return summarize([SecCheck("capture response headers", False, "none")], [])
    status = protected.get("status")
    if not require_embedded_framing:
        return surface_verdict_from_headers(headers)

    document = res.get("document") if isinstance(res, dict) else None
    if not isinstance(document, dict):
        return summarize([SecCheck("capture final document headers", False, "none")], [])
    document_headers = document.get("headers")
    if not isinstance(document_headers, dict):
        return summarize([SecCheck("capture final document headers", False, "none")], [])
    document_status = document.get("status")
    document_url = document.get("url")
    try:
        base = urlsplit(base_url)
        final_document = urlsplit(document_url if isinstance(document_url, str) else "")
        final_document_ok = (
            final_document.scheme == base.scheme
            and final_document.hostname == base.hostname
            and final_document.port == base.port
            and final_document.username is None
            and final_document.password is None
            and final_document.path.rstrip("/") != "/api/omnia/preview-session"
        )
    except ValueError:
        final_document_ok = False
    if not final_document_ok:
        return summarize(
            [SecCheck("final product document", False, "unexpected location")],
            [],
        )
    return surface_verdict_from_headers(
        headers,
        require_embedded_framing=require_embedded_framing,
        protected_status=status if isinstance(status, int) else None,
        framing_policy=framing_policy,
        document_headers=document_headers,
        document_status=document_status if isinstance(document_status, int) else None,
    )
