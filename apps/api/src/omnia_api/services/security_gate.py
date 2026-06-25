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
