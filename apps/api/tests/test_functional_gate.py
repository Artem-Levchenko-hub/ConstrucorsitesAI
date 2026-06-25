"""Unit tests for the pure verdict aggregation of the functional+security gate.

The browser-driven flow needs a live preview, so it is exercised in integration
runs; here we lock down the SHIP/NO-SHIP logic — especially that a single failed
check (e.g. one leak) fails the whole gate, since security has no partial credit.
"""

from __future__ import annotations

from omnia_api.services.functional_gate import Check, summarize


def test_all_pass_ships() -> None:
    checks = [
        Check("login", True),
        Check("live delivery", True),
        Check("outsider denied", True),
    ]
    verdict = summarize(checks)
    assert verdict.passed is True
    assert "PASSED" in verdict.summary


def test_single_failure_blocks_ship() -> None:
    checks = [
        Check("login", True),
        Check("live delivery", True),
        Check("outsider DENIED history (403)", False, "200"),  # a leak!
    ]
    verdict = summarize(checks)
    assert verdict.passed is False
    assert "FAILED" in verdict.summary
    assert "outsider DENIED history (403)" in verdict.summary


def test_no_checks_is_not_a_pass() -> None:
    # A gate that ran zero checks (e.g. the app never came up) must NOT count as
    # a pass — "no evidence" is not "proven safe".
    verdict = summarize([])
    assert verdict.passed is False


def test_missed_live_delivery_fails() -> None:
    checks = [
        Check("login", True),
        Check("student receives message live (<1s, SSE)", False, "timed out"),
    ]
    verdict = summarize(checks)
    assert verdict.passed is False
    assert "live" in verdict.summary
