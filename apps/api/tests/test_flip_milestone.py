"""V1.6 16/5e — deterministic coverage for the flip-milestone harness.

Every test here runs WITHOUT a browser, an LLM, or an orchestrator: the corpus
is scored with injected fake gauntlet/route fns (the niche_batch seam) and the
decision is pure. The standing teeth — ``test_committed_flag_default_is_consistent``
and ``test_flag_on_without_milestone_is_caught`` — guard the real config flag:
flipping ``acceptance_entity_composition_gate`` ON without a passing milestone
turns this suite RED.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import flip_milestone as fm  # noqa: E402
import niche_batch as nb  # noqa: E402

# ── fakes (mirror niche_batch's verdict surface) ─────────────────────────────


class _FakeGate:
    def __init__(self, gate: str, passed: bool, classes: tuple[str, ...] = ()) -> None:
        self.gate = gate
        self.passed = passed
        self.classes = classes


class _FakeVerdict:
    def __init__(self, gates: list[_FakeGate]) -> None:
        self.gates = tuple(gates)

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def failed_classes(self) -> tuple[str, ...]:
        out: list[str] = []
        for g in self.gates:
            for c in g.classes:
                out.append(f"{g.gate}:{c}")
        return tuple(out)

    def subscore(self) -> dict:
        return {"passed": self.passed, "failed_classes": list(self.failed_classes)}


def _clean() -> _FakeVerdict:
    return _FakeVerdict([_FakeGate("taste", True), _FakeGate("hierarchy", True)])


def _broken() -> _FakeVerdict:
    return _FakeVerdict(
        [_FakeGate("taste", False, ("hero-imagery",)), _FakeGate("hierarchy", True)]
    )


def _gauntlet(verdict_for):
    async def _run(*, url, **_kw):
        return verdict_for(url)

    return _run


def _route(value: str = "/"):
    async def _resolve(_base, *, candidate_route="/dashboard"):
        return value

    return _resolve


def _good_urls() -> dict[str, str]:
    return {n.key: f"http://omnia-dev-{n.key}:3000" for n in nb.CORPUS}


# ── pure decision: evaluate_flip ─────────────────────────────────────────────


def _tally(all_passed: bool, new_classes: list[str] | None = None) -> dict:
    return {
        "all_passed": all_passed,
        "per_niche": {"sushi": {"pass_rate": 1.0 if all_passed else 0.5}},
        "new_defect_classes": new_classes or [],
    }


def test_flip_permitted_when_corpus_passes_baseline_fails_freeform_ok() -> None:
    v = fm.evaluate_flip(_tally(True), baseline_passed=False, freeform_ok=True)
    assert v.flip_permitted is True
    assert v.reasons == ()


def test_flip_blocked_when_corpus_false_fails() -> None:
    v = fm.evaluate_flip(_tally(False), baseline_passed=False)
    assert v.flip_permitted is False
    assert any("corpus did NOT all-pass" in r for r in v.reasons)
    assert "failing: sushi" in " ".join(v.reasons)


def test_flip_blocked_when_baseline_passes_no_teeth() -> None:
    v = fm.evaluate_flip(_tally(True), baseline_passed=True)
    assert v.flip_permitted is False
    assert any("no teeth" in r for r in v.reasons)


def test_flip_blocked_when_freeform_regressed() -> None:
    v = fm.evaluate_flip(_tally(True), baseline_passed=False, freeform_ok=False)
    assert v.flip_permitted is False
    assert any("freeform" in r for r in v.reasons)


def test_new_defect_class_blocks_even_if_all_passed() -> None:
    # A fresh defect class is a floor regression even if every run technically passed.
    v = fm.evaluate_flip(
        _tally(True, new_classes=["taste:weird"]), baseline_passed=False
    )
    assert v.corpus_all_passed is False
    assert v.flip_permitted is False
    assert any("new defect class" in r for r in v.reasons)


# ── pure decision: check_consistency (the exit-1 contract) ───────────────────


def test_flag_off_is_always_consistent() -> None:
    blocked = fm.evaluate_flip(_tally(False), baseline_passed=True)
    ok, msg = fm.check_consistency(False, blocked)
    assert ok is True
    assert "OFF" in msg


def test_flag_on_with_passing_milestone_is_consistent() -> None:
    permitted = fm.evaluate_flip(_tally(True), baseline_passed=False)
    ok, _ = fm.check_consistency(True, permitted)
    assert ok is True


def test_flag_on_without_milestone_is_inconsistent() -> None:
    blocked = fm.evaluate_flip(_tally(False), baseline_passed=True)
    ok, msg = fm.check_consistency(True, blocked)
    assert ok is False
    assert "does NOT pass" in msg


# ── evidence: run_milestone over the niche_batch seam ────────────────────────


async def test_run_milestone_permits_flip_on_good_corpus_failing_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Good corpus apps pass; the baseline app (its URL contains "baseline") fails.
    monkeypatch.setattr(
        nb.accept_gauntlet,
        "run",
        _gauntlet(lambda url: _broken() if "baseline" in url else _clean()),
    )
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))

    verdict, evidence = await fm.run_milestone(
        _good_urls(), "http://omnia-dev-baseline:3000"
    )
    assert verdict.flip_permitted is True
    assert evidence["corpus_tally"]["all_passed"] is True
    assert evidence["baseline"]["passed"] is False


async def test_run_milestone_blocks_when_a_good_niche_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nb.accept_gauntlet,
        "run",
        _gauntlet(lambda url: _broken() if ("shop" in url or "baseline" in url) else _clean()),
    )
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))

    verdict, _ = await fm.run_milestone(
        _good_urls(), "http://omnia-dev-baseline:3000"
    )
    assert verdict.flip_permitted is False
    assert verdict.corpus_all_passed is False


async def test_run_milestone_blocks_when_baseline_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nb.accept_gauntlet, "run", _gauntlet(lambda _url: _clean()))
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))

    verdict, _ = await fm.run_milestone(
        _good_urls(), "http://omnia-dev-baseline:3000"
    )
    assert verdict.baseline_failed is False
    assert verdict.flip_permitted is False


async def test_missing_baseline_blocks_flip_teeth_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nb.accept_gauntlet, "run", _gauntlet(lambda _url: _clean()))
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))

    verdict, evidence = await fm.run_milestone(_good_urls(), None)
    assert verdict.baseline_failed is False  # no proof of teeth
    assert verdict.flip_permitted is False
    assert evidence["baseline_provided"] is False


# ── standing teeth: the committed config flag must stay consistent ───────────


def test_committed_flag_default_is_consistent() -> None:
    """The real flag, at its committed default, must be a consistent state.

    Today the flag is OFF → consistent regardless of milestone. The day someone
    flips it ON in config without recording a passing milestone, this assertion
    (paired with the guard below) is the thing that should already have caught it.
    """
    from omnia_api.core.config import get_settings

    flag_on = bool(getattr(get_settings(), fm.FLAG_NAME))
    # Worst-case verdict: nothing proven. OFF must still be consistent.
    blocked = fm.evaluate_flip(_tally(False), baseline_passed=True)
    ok, msg = fm.check_consistency(flag_on, blocked)
    if flag_on:
        # If the owner flipped it ON, there MUST be a recorded passing milestone —
        # this test then requires the real corpus evidence to exist (see runbook).
        pytest.fail(
            f"{fm.FLAG_NAME} is ON but no milestone evidence is wired into the test "
            f"suite. Record the passing flip-milestone before enabling the flag. {msg}"
        )
    assert ok is True


def test_flag_on_without_milestone_is_caught() -> None:
    """Regression guard: an ON flag with a failing milestone is exit-1."""
    blocked = fm.evaluate_flip(_tally(False), baseline_passed=True)
    ok, _ = fm.check_consistency(True, blocked)
    assert ok is False


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_guard_mode_green_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        nb.accept_gauntlet,
        "run",
        _gauntlet(lambda url: _broken() if "baseline" in url else _clean()),
    )
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))
    urls = tmp_path / "urls.json"
    urls.write_text(json.dumps(_good_urls()), encoding="utf-8")

    code = fm.main(
        [
            "--urls",
            str(urls),
            "--baseline-url",
            "http://omnia-dev-baseline:3000",
            "--flag-off",
            "--mode",
            "guard",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["consistent"] is True
    assert out["milestone"]["flip_permitted"] is True


def test_cli_gate_mode_exit1_when_milestone_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # Baseline passes ⇒ no teeth ⇒ gate mode must refuse to authorize the flip.
    monkeypatch.setattr(nb.accept_gauntlet, "run", _gauntlet(lambda _url: _clean()))
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))
    urls = tmp_path / "urls.json"
    urls.write_text(json.dumps(_good_urls()), encoding="utf-8")

    code = fm.main(
        [
            "--urls",
            str(urls),
            "--baseline-url",
            "http://omnia-dev-baseline:3000",
            "--mode",
            "gate",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["milestone"]["flip_permitted"] is False


def test_cli_guard_mode_exit1_when_flag_forced_on_without_milestone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(nb.accept_gauntlet, "run", _gauntlet(lambda _url: _clean()))
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/dashboard"))
    urls = tmp_path / "urls.json"
    urls.write_text(json.dumps(_good_urls()), encoding="utf-8")

    # Flag forced ON, baseline omitted ⇒ teeth unproven ⇒ inconsistent ⇒ exit 1.
    code = fm.main(["--urls", str(urls), "--flag-on", "--mode", "guard"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["consistent"] is False
