"""Unit tests for the multi-role enforcement matrix (G007)."""

from __future__ import annotations

from omnia_api.services.role_gate import (
    RoleExpectation,
    evaluate_cell,
    evaluate_matrix,
)


def _exp(role: str, op: str, expect: str) -> RoleExpectation:
    return RoleExpectation(role=role, op=op, entity="Grade", expect=expect)


def test_correct_role_allowed_passes() -> None:
    assert evaluate_cell(_exp("teacher", "write", "allow"), 200).ok is True


def test_wrong_role_denied_passes() -> None:
    # student blocked from writing a teacher-only entity → 403 is the PASS.
    assert evaluate_cell(_exp("student", "write", "deny"), 403).ok is True


def test_leak_fails() -> None:
    # student write that should be denied but succeeded (200) = a leak → FAIL.
    assert evaluate_cell(_exp("student", "write", "deny"), 200).ok is False


def test_broken_role_fails() -> None:
    # teacher write that should be allowed but got 403 = broken role gate → FAIL.
    assert evaluate_cell(_exp("teacher", "write", "allow"), 403).ok is False


def test_denied_must_be_401_or_403_not_404() -> None:
    # A denied op returning 404/500 is a bug, not a clean deny — do not pass it.
    assert evaluate_cell(_exp("student", "write", "deny"), 404).ok is False


def test_matrix_passes_only_when_all_cells_pass() -> None:
    results = [
        (_exp("teacher", "write", "allow"), 200),
        (_exp("student", "write", "deny"), 403),
        (_exp("student", "read", "allow"), 200),
    ]
    assert evaluate_matrix(results).passed is True

    leaky = results + [(_exp("parent", "write", "deny"), 200)]  # a leak
    verdict = evaluate_matrix(leaky)
    assert verdict.passed is False
    assert "parent write Grade" in verdict.summary


def test_empty_matrix_is_not_a_pass() -> None:
    assert evaluate_matrix([]).passed is False
