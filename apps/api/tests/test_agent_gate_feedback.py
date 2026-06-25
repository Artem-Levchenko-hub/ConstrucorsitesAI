"""Unit tests for the gate-feedback self-heal logic (layer C)."""

from __future__ import annotations

from omnia_api.services.agent_gate_feedback import (
    GateOutcome,
    all_passed,
    build_fix_instruction,
    should_retry,
)


def _g(name: str, passed: bool, failures=None) -> GateOutcome:
    return GateOutcome(name=name, passed=passed, failures=failures or [])


def test_all_green_no_instruction() -> None:
    outs = [_g("backend_guardrail", True), _g("security", True)]
    assert all_passed(outs)
    assert build_fix_instruction(outs, attempt=0, max_attempts=2) is None


def test_red_gate_yields_concrete_instruction() -> None:
    outs = [
        _g("security", False, ["outsider DENIED history (403): got 200"]),
        _g("backend_guardrail", False, ["src/app/x/route.ts: raw @/lib/db import"]),
    ]
    instr = build_fix_instruction(outs, attempt=0, max_attempts=2)
    assert instr is not None
    assert "security" in instr and "backend_guardrail" in instr
    assert "outsider DENIED history (403): got 200" in instr
    assert "src/app/x/route.ts" in instr


def test_out_of_retries_stops() -> None:
    outs = [_g("functional", False, ["live delivery timed out"])]
    assert build_fix_instruction(outs, attempt=2, max_attempts=2) is None
    assert should_retry(outs, attempt=2, max_attempts=2) is False


def test_retry_while_budget_remains() -> None:
    outs = [_g("functional", False, ["x"])]
    assert should_retry(outs, attempt=0, max_attempts=2) is True
    assert should_retry(outs, attempt=1, max_attempts=2) is True


def test_non_blocking_failure_does_not_force_retry() -> None:
    outs = [GateOutcome("advisory", passed=False, failures=["nit"], blocking=False)]
    assert build_fix_instruction(outs, attempt=0, max_attempts=2) is None
    assert should_retry(outs, attempt=0, max_attempts=2) is False


class _Check:
    def __init__(self, name: str, ok: bool, detail: str = "") -> None:
        self.name = name
        self.ok = ok
        self.detail = detail


def test_outcome_from_checks_maps_only_failures() -> None:
    from omnia_api.services.agent_gate_feedback import outcome_from_checks

    checks = [
        _Check("signup", True),
        _Check("outsider DENIED history", False, "got 200"),
        _Check("live delivery", False, ""),
    ]
    o = outcome_from_checks("functional", False, checks)
    assert o.name == "functional" and o.passed is False
    assert "outsider DENIED history: got 200" in o.failures
    assert "live delivery" in o.failures  # empty detail trims cleanly, no trailing ": "
    assert len(o.failures) == 2  # the passed check is excluded


def test_outcome_from_checks_all_pass() -> None:
    from omnia_api.services.agent_gate_feedback import outcome_from_checks

    o = outcome_from_checks("functional", True, [_Check("a", True), _Check("b", True)])
    assert o.passed is True and o.failures == []
