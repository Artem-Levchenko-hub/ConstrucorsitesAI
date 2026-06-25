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
