"""Unit tests for services.coverage_gate — pure status logic + fail-soft exits.

The browser happy-path (login + per-capability fetch) is integration-level and
needs a live preview; here we cover the pure decision logic (status_matches,
verdict assembly) and the fail-soft early-exits that guarantee the gate can never
block a build on its own infrastructure (no caps / bad id / no preview → SKIPPED,
passed=True).
"""

from __future__ import annotations

import uuid

from omnia_api.services.build_plan import BuildPlan, Capability
from omnia_api.services.coverage_gate import (
    CoverageCheck,
    _verdict_from_checks,
    run_coverage_gate,
    status_matches,
)


def test_status_matches_class_and_exact():
    assert status_matches(200, "2xx")
    assert status_matches(201, "2xx")
    assert not status_matches(404, "2xx")
    assert status_matches(403, "403")
    assert not status_matches(404, "403")
    assert status_matches(404, "4xx")
    assert not status_matches(500, "4xx")
    assert status_matches(503, "5xx")


def test_status_matches_defaults_and_network_throw():
    # empty / unparseable expectation → happy path (2xx)
    assert status_matches(200, "")
    assert status_matches(200, "weird")
    # a 0 (request threw at the network layer) never satisfies a 2xx expectation
    assert not status_matches(0, "2xx")


def test_verdict_from_checks():
    cks = [
        CoverageCheck(True, "a"),
        CoverageCheck(False, "b"),
        CoverageCheck(True, "c"),
    ]
    v = _verdict_from_checks(cks)
    assert v.total == 3
    assert v.covered == 2
    assert v.missing == ["b"]
    assert not v.passed
    v2 = _verdict_from_checks([CoverageCheck(True, "a")])
    assert v2.passed and v2.covered == 1 and v2.missing == []


async def test_no_blocking_caps_skipped():
    # a UI-only capability (no path) is not probeable → nothing to block on
    plan = BuildPlan(capabilities=(Capability(id="ui", path="", must_have=True),))
    v = await run_coverage_gate(str(uuid.uuid4()), plan)
    assert v.skipped and v.passed and v.total == 0


async def test_bad_project_id_skipped():
    plan = BuildPlan(capabilities=(Capability(id="c", path="/api/x", must_have=True),))
    v = await run_coverage_gate("not-a-uuid", plan)
    assert v.skipped and v.passed


async def test_no_dev_url_skipped(monkeypatch):
    async def _status(_pid):
        return {}

    monkeypatch.setattr(
        "omnia_api.services.orchestrator_client.get_status", _status
    )
    plan = BuildPlan(capabilities=(Capability(id="c", path="/api/x", must_have=True),))
    v = await run_coverage_gate(str(uuid.uuid4()), plan)
    assert v.skipped and v.passed
