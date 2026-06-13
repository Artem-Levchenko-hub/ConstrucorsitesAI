"""V1.6 6/5 — deterministic coverage for the batch niche-runner.

Everything here runs WITHOUT a browser, an LLM, or an orchestrator: the runner's
scoring is injected with fake gauntlet/route fns and the tally is pure. That is
the contract — the ratchet must be replayable on a fixed-seed corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The runner lives in scripts/ (the plan's literal path), not src/.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import niche_batch as nb  # noqa: E402

# ── fakes ────────────────────────────────────────────────────────────────────


class _FakeGate:
    def __init__(self, gate: str, passed: bool, classes: tuple[str, ...] = ()) -> None:
        self.gate = gate
        self.passed = passed
        self.classes = classes


class _FakeVerdict:
    """Mimics the public surface of accept_gauntlet.GauntletVerdict."""

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


def _clean_verdict() -> _FakeVerdict:
    return _FakeVerdict([_FakeGate("taste", True), _FakeGate("hierarchy", True)])


def _broken_verdict() -> _FakeVerdict:
    return _FakeVerdict(
        [_FakeGate("taste", False, ("hero-imagery",)), _FakeGate("hierarchy", True)]
    )


def _gauntlet(verdict: _FakeVerdict):
    async def _run(**_kw):
        return verdict

    return _run


def _route(value: str = "/"):
    async def _resolve(_base, *, candidate_route="/dashboard"):
        return value

    return _resolve


# ── corpus / spec ────────────────────────────────────────────────────────────


def test_corpus_is_fixed_seed_five_niches() -> None:
    assert [n.key for n in nb.CORPUS] == ["sushi", "fitness", "shop", "school", "fintech"]


def test_niche_spec_is_deterministic_and_parses_chips() -> None:
    spec = {n.key: n.spec() for n in nb.CORPUS}["fintech"]
    assert spec.dark_mode is True
    assert spec.primary_family == "violet"
    assert spec.tone == "premium"
    # same niche, same spec — no per-call randomness
    assert nb.CORPUS[-1].spec() == nb.CORPUS[-1].spec()


def test_select_niches_prefix_and_explicit() -> None:
    assert [n.key for n in nb.select_niches(None, 2)] == ["sushi", "fitness"]
    assert [n.key for n in nb.select_niches(["shop", "sushi"], None)] == ["shop", "sushi"]
    with pytest.raises(ValueError):
        nb.select_niches(["nope"], None)


# ── scoring ──────────────────────────────────────────────────────────────────


async def test_score_app_runs_every_width_on_resolved_route() -> None:
    seen: dict = {"urls": [], "widths": [], "comp_widths": []}

    async def _run(**kw):
        seen["urls"].append(kw["url"])
        seen["widths"].append(kw["width"])
        seen["comp_widths"].append(kw["composition_width"])
        assert kw["composition"] is True and kw["include_rendered"] is False
        return _clean_verdict()

    res = await nb.score_app(
        "http://omnia-dev-fin:3000",
        nb.CORPUS[-1],
        gauntlet_run=_run,
        resolve_route=_route("/dashboard"),
    )
    assert res.route == "/dashboard"
    assert seen["urls"] == ["http://omnia-dev-fin:3000/dashboard"] * 2
    assert seen["widths"] == [nb.DESKTOP_WIDTH, nb.MOBILE_WIDTH]
    # composition legs follow the iteration viewport — the @390 pass renders the
    # richness/hierarchy legs at mobile too (V1.6 15/5), not a desktop duplicate.
    assert seen["comp_widths"] == [nb.DESKTOP_WIDTH, nb.MOBILE_WIDTH]
    assert res.passed is True
    assert res.min_score == 1.0


async def test_score_app_root_route_composes_clean_url() -> None:
    captured: dict = {}

    async def _run(**kw):
        captured["url"] = kw["url"]
        return _clean_verdict()

    await nb.score_app(
        "http://omnia-dev-shop:3000/",
        nb.CORPUS[2],
        widths=(nb.DESKTOP_WIDTH,),
        gauntlet_run=_run,
        resolve_route=_route("/"),
    )
    assert captured["url"] == "http://omnia-dev-shop:3000/"


async def test_score_app_failing_width_fails_run_and_lowers_score() -> None:
    res = await nb.score_app(
        "http://x:3000",
        nb.CORPUS[0],
        widths=(nb.DESKTOP_WIDTH, nb.MOBILE_WIDTH),
        gauntlet_run=_gauntlet(_broken_verdict()),
        resolve_route=_route("/"),
    )
    assert res.passed is False
    assert res.min_score == 0.5  # 1 of 2 gates passed
    assert res.failed_classes == ("taste:hero-imagery",)


async def test_run_niche_provision_miss_is_a_scored_fail() -> None:
    async def _provision(_niche, _i):
        return None

    out = await nb.run_niche(nb.CORPUS[0], 2, _provision)
    assert len(out) == 2
    assert all(not r.passed and r.min_score == 0.0 and r.base_url is None for r in out)


# ── tally ────────────────────────────────────────────────────────────────────


def _run(niche: str, passed: bool, score: float, classes=()) -> nb.RunResult:
    w = nb.WidthResult(1440, passed, tuple(classes), score, {})
    return nb.RunResult(niche, 0, "http://x", "/", (w,))


def test_tally_pass_rate_per_niche_min_and_all_passed() -> None:
    results = [
        _run("sushi", True, 1.0),
        _run("sushi", True, 1.0),
        _run("shop", True, 1.0),
        _run("shop", False, 0.5, ("taste:hero-imagery",)),
    ]
    t = nb.tally(results, niches=2, runs_per_niche=2)
    assert t["total_runs"] == 4
    assert t["passed"] == 3
    assert t["pass_rate"] == 0.75
    assert t["all_passed"] is False
    assert t["per_niche"]["sushi"]["pass_rate"] == 1.0
    assert t["per_niche"]["sushi"]["min_score"] == 1.0
    assert t["per_niche"]["shop"]["min_score"] == 0.5
    assert t["per_niche"]["shop"]["failed_classes"] == ["taste:hero-imagery"]


def test_tally_all_passed_true_when_every_run_green() -> None:
    t = nb.tally([_run("sushi", True, 1.0)], niches=1, runs_per_niche=1)
    assert t["all_passed"] is True
    assert t["new_defect_class_count"] == 0


def test_tally_new_defect_classes_are_those_outside_baseline() -> None:
    results = [
        _run("a", False, 0.5, ("taste:hero-imagery",)),
        _run("b", False, 0.5, ("hierarchy:focal-dominance",)),
    ]
    t = nb.tally(
        results,
        niches=2,
        runs_per_niche=1,
        known_classes=["taste:hero-imagery"],
    )
    assert t["defect_classes"] == ["hierarchy:focal-dominance", "taste:hero-imagery"]
    assert t["new_defect_classes"] == ["hierarchy:focal-dominance"]
    assert t["new_defect_class_count"] == 1


def test_tally_is_json_serializable() -> None:
    t = nb.tally([_run("sushi", True, 1.0)], niches=1, runs_per_niche=1)
    assert json.loads(json.dumps(t))["pass_rate"] == 1.0


# ── provisioner / CLI (money-free) ───────────────────────────────────────────


async def test_urls_provisioner_maps_known_and_returns_none_for_unknown() -> None:
    prov = nb.urls_provisioner({"sushi": "http://omnia-dev-sushi:3000"})
    assert await prov(nb.CORPUS[0], 0) == "http://omnia-dev-sushi:3000"
    assert await prov(nb.CORPUS[1], 0) is None  # fitness not in map


def test_cli_scores_urls_and_exit_code_tracks_all_passed(monkeypatch, tmp_path, capsys) -> None:
    # Score path is forced green via an injected gauntlet — no browser, no LLM.
    monkeypatch.setattr(nb.accept_gauntlet, "run", _gauntlet(_clean_verdict()))
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/"))

    urls = tmp_path / "urls.json"
    urls.write_text(json.dumps({"sushi": "http://omnia-dev-sushi:3000"}))
    out = tmp_path / "tally.json"

    code = nb.main(["--urls", str(urls), "--only", "sushi", "--out", str(out)])
    assert code == 0
    report = json.loads(out.read_text())
    assert report["all_passed"] is True
    assert report["per_niche"]["sushi"]["pass_rate"] == 1.0
    assert "sushi" in capsys.readouterr().out


def test_cli_exit_1_when_a_run_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(nb.accept_gauntlet, "run", _gauntlet(_broken_verdict()))
    monkeypatch.setattr(nb.route_target, "resolve_target_route", _route("/"))
    urls = tmp_path / "urls.json"
    urls.write_text(json.dumps({"sushi": "http://omnia-dev-sushi:3000"}))
    assert nb.main(["--urls", str(urls), "--only", "sushi"]) == 1
