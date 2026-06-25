"""Multi-role enforcement gate (G007).

The role-graph PRIMITIVE already exists in the entity engine (`readRoles` /
`writeRoles` — e.g. only `teacher` writes a Grade, `teacher/student/parent` read
it). This gate PROVES that primitive actually enforces: it drives a live entities
app and checks a role MATRIX — the right role is allowed, the wrong role is denied
(403), and a denied write does not silently succeed.

`evaluate_matrix` (pure) is the verifiable core, unit-tested without a browser.
`run_role_gate` drives the engine's REST contract (POST/GET /api/entities/<E>)
across two role sessions. Gated by ``Settings.use_role_gate``; applies to
entity-engine apps that declare role gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoleExpectation:
    """One cell of the role matrix: role `role` doing `op` on `entity` should get
    `expect` ('allow' → 2xx, 'deny' → 403)."""

    role: str
    op: str        # "read" | "write"
    entity: str
    expect: str    # "allow" | "deny"


@dataclass
class RoleCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RoleVerdict:
    passed: bool
    checks: list[RoleCheck] = field(default_factory=list)
    summary: str = ""


def _status_matches(expect: str, status: int) -> bool:
    if expect == "allow":
        return 200 <= status < 300
    if expect == "deny":
        # A denied op MUST be 401/403 — anything 2xx is a leak, a 404/500 is a bug
        # that we also refuse to pass (a denied write must be cleanly rejected).
        return status in (401, 403)
    return False


def evaluate_cell(exp: RoleExpectation, status: int) -> RoleCheck:
    """Turn one matrix cell + the observed HTTP status into a pass/fail check.
    Pure — the unit-tested heart of the gate."""
    ok = _status_matches(exp.expect, status)
    verb = "allowed" if exp.expect == "allow" else "DENIED"
    return RoleCheck(
        name=f"{exp.role} {exp.op} {exp.entity} -> {verb}",
        ok=ok,
        detail=str(status),
    )


def evaluate_matrix(results: list[tuple[RoleExpectation, int]]) -> RoleVerdict:
    """Aggregate every matrix cell into a verdict. A single wrong outcome
    (allowed-when-should-deny = leak, or denied-when-should-allow = broken role)
    fails the whole gate; zero cells is not a pass."""
    checks = [evaluate_cell(exp, status) for exp, status in results]
    failures = [c for c in checks if not c.ok]
    passed = len(checks) > 0 and not failures
    if passed:
        summary = f"role enforcement PASSED ({len(checks)} cells)"
    else:
        names = ", ".join(c.name for c in failures) or "no cells evaluated"
        summary = f"role enforcement FAILED: {names}"
    return RoleVerdict(passed=passed, checks=checks, summary=summary)
