"""Perf + a11y gate — the blocking speed/accessibility floor (V1.6 slice 3/5).

The gate is "JS extracts, Python scores", so the entire verdict — Lighthouse-style
perf curve and axe violation policy — is exercised here with hand-built
observation dicts, no browser. Each check has a CLEAN case (passes) and a RED case
(exactly that check fires), the same falsifiable contract as slices 1 and 2: the
floor only goes up.
"""

import math

from omnia_api.services import perf_a11y_gate as g
from omnia_api.services.perf_a11y_gate import (
    evaluate_observation,
    lighthouse_perf_score,
    log_normal_score,
)

# Metric values that sail past every floor (a fast, stable page).
FAST = {"ttfb": 120.0, "fcp": 700.0, "lcp": 900.0, "tbt": 30.0, "cls": 0.01}


def _obs(metrics=None, violations=None, axe_ran=True):
    axe = {"ran": axe_ran, "violations": violations or []}
    return {"metrics": dict(FAST if metrics is None else metrics), "axe": axe}


def _viol(impact, rule="some-rule", nodes=1, help="fix it"):
    return {
        "id": rule,
        "impact": impact,
        "help": help,
        "helpUrl": "https://x",
        "nodes": [{"target": [f"#n{i}"], "html": "<x>"} for i in range(nodes)],
    }


# ── Lighthouse log-normal curve ────────────────────────────────────────────────


def test_log_normal_score_zero_is_perfect():
    assert log_normal_score(2500, 4000, 0) == 1.0


def test_log_normal_score_at_p10_is_0_9():
    # Lighthouse's control-point contract: value == p10 scores exactly 0.90.
    assert round(log_normal_score(2500, 4000, 2500), 3) == 0.9


def test_log_normal_score_at_median_is_0_5():
    assert round(log_normal_score(2500, 4000, 4000), 3) == 0.5


def test_log_normal_score_monotonic_decreasing():
    fast = log_normal_score(200, 600, 100)
    mid = log_normal_score(200, 600, 400)
    slow = log_normal_score(200, 600, 1200)
    assert fast > mid > slow
    assert 0.0 <= slow < 0.5 < fast <= 1.0


def test_log_normal_score_malformed_points_no_credit():
    assert log_normal_score(0, 100, 50) == 0.0
    assert log_normal_score(100, 100, 50) == 0.0  # p10 >= median


# ── perf score rollup ──────────────────────────────────────────────────────────


def test_perf_score_fast_page_is_high():
    assert lighthouse_perf_score(FAST) >= 95


def test_perf_score_all_metrics_at_p10_is_90():
    # Every metric exactly at its p10 → each sub-score 0.90 → rollup 90.
    at_p10 = {"fcp": 1800, "lcp": 2500, "tbt": 200, "cls": 0.1}
    assert lighthouse_perf_score(at_p10) == 90


def test_perf_score_all_metrics_at_median_is_50():
    at_median = {"fcp": 3000, "lcp": 4000, "tbt": 600, "cls": 0.25}
    assert lighthouse_perf_score(at_median) == 50


def test_perf_score_none_without_metrics():
    assert lighthouse_perf_score({}) is None


def test_perf_score_partial_metrics_renormalises():
    # Only LCP present, at median → that single weighted sub-score → 50.
    assert lighthouse_perf_score({"lcp": 4000}) == 50


# ── ttfb floor ─────────────────────────────────────────────────────────────────


def test_ttfb_clean():
    rep = evaluate_observation(_obs())
    assert g.SLOW_TTFB not in rep.classes


def test_ttfb_red():
    rep = evaluate_observation(_obs({**FAST, "ttfb": 1200.0}))
    assert g.SLOW_TTFB in rep.classes
    assert not rep.passed


def test_ttfb_exactly_at_floor_is_red():
    rep = evaluate_observation(_obs({**FAST, "ttfb": 800.0}))
    assert g.SLOW_TTFB in rep.classes


# ── lcp floor ──────────────────────────────────────────────────────────────────


def test_lcp_clean():
    assert g.SLOW_LCP not in evaluate_observation(_obs()).classes


def test_lcp_red():
    rep = evaluate_observation(_obs({**FAST, "lcp": 4200.0}))
    assert g.SLOW_LCP in rep.classes
    assert not rep.passed


# ── cls floor ──────────────────────────────────────────────────────────────────


def test_cls_clean():
    assert g.LAYOUT_SHIFT not in evaluate_observation(_obs()).classes


def test_cls_red():
    rep = evaluate_observation(_obs({**FAST, "cls": 0.3}))
    assert g.LAYOUT_SHIFT in rep.classes
    assert not rep.passed


# ── perf score floor ───────────────────────────────────────────────────────────


def test_low_perf_clean():
    assert g.LOW_PERF not in evaluate_observation(_obs()).classes


def test_low_perf_red():
    # Heavy main-thread + late hero but TTFB/LCP/CLS each just under their own
    # floor → only the aggregate perf score trips, proving it's an independent gate.
    slow = {"ttfb": 700.0, "fcp": 3200.0, "lcp": 2400.0, "tbt": 1500.0, "cls": 0.09}
    rep = evaluate_observation(_obs(slow))
    assert g.LOW_PERF in rep.classes
    assert rep.perf_score is not None and rep.perf_score < g.PERF_SCORE_FLOOR
    assert not rep.passed


# ── a11y (axe) policy ──────────────────────────────────────────────────────────


def test_a11y_clean_no_violations():
    rep = evaluate_observation(_obs(violations=[]))
    assert rep.passed
    assert rep.axe_violation_count == 0


def test_a11y_critical_red():
    rep = evaluate_observation(_obs(violations=[_viol("critical", "button-name")]))
    assert g.A11Y_VIOLATION in rep.classes
    assert rep.axe_violation_count == 1
    assert not rep.passed


def test_a11y_serious_red():
    rep = evaluate_observation(_obs(violations=[_viol("serious", "image-alt")]))
    assert g.A11Y_VIOLATION in rep.classes
    assert not rep.passed


def test_a11y_minor_and_moderate_ignored():
    rep = evaluate_observation(
        _obs(violations=[_viol("minor", "region"), _viol("moderate", "landmark")])
    )
    assert g.A11Y_VIOLATION not in rep.classes
    assert rep.axe_violation_count == 0
    assert rep.passed


def test_a11y_abstain_when_axe_did_not_run():
    # axe failed to load (CSP/missing engine) → a11y abstains, perf still scored.
    obs = {"metrics": dict(FAST), "axe": {"ran": False, "violations": []}}
    rep = evaluate_observation(obs)
    assert rep.a11y_ran is False
    assert g.A11Y_VIOLATION not in rep.classes
    # perf is clean, so with a11y abstaining the gate still passes (no findings).
    assert rep.passed


def test_a11y_findings_capped():
    viols = [_viol("critical", f"rule-{i}") for i in range(30)]
    rep = evaluate_observation(_obs(violations=viols))
    assert rep.counts[g.A11Y_VIOLATION] <= g._MAX_PER_CHECK
    # but the true count is reported in full for the subscore.
    assert rep.axe_violation_count == 30


# ── report / abstain semantics ─────────────────────────────────────────────────


def test_not_rendered_abstains():
    rep = evaluate_observation({}, rendered=False)
    assert rep.rendered is False
    assert rep.passed is False  # abstain is not pass
    assert rep.perf_score is None


def test_subscore_shape():
    rep = evaluate_observation(_obs({**FAST, "lcp": 4200.0}, violations=[_viol("serious")]))
    sub = rep.subscore()
    assert sub["gate"] == "perf-a11y"
    assert sub["passed"] is False
    assert sub["rendered"] is True
    assert sub["a11y_ran"] is True
    assert set(sub["counts"]) == set(g.CHECKS)
    assert sub["counts"][g.SLOW_LCP] == 1
    assert sub["counts"][g.A11Y_VIOLATION] == 1
    assert sub["axe_violations"] == 1
    assert sub["perf_score"] == rep.perf_score


def test_multiple_findings_all_surface():
    rep = evaluate_observation(
        _obs({**FAST, "ttfb": 1100.0, "lcp": 5000.0}, violations=[_viol("critical")])
    )
    assert {g.SLOW_TTFB, g.SLOW_LCP, g.A11Y_VIOLATION} <= set(rep.classes)
    assert len(rep.findings) >= 3


def test_summary_clean_and_dirty():
    assert "clean" in evaluate_observation(_obs()).summary()
    dirty = evaluate_observation(_obs({**FAST, "lcp": 5000.0})).summary()
    assert "slow-lcp" in dirty


def test_non_numeric_metrics_dropped():
    obs = {
        "metrics": {"ttfb": "abc", "lcp": None, "cls": 0.02},
        "axe": {"ran": True, "violations": []},
    }
    rep = evaluate_observation(obs)
    assert "ttfb" not in rep.metrics
    assert rep.metrics["cls"] == 0.02


def test_erfc_sanity():
    # Guard the constant: erfc(0) == 1 underpins the median→0.5 contract.
    assert math.erfc(0) == 1.0
