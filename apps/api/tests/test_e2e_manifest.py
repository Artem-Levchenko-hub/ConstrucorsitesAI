"""PAID-RUN MANIFEST — deterministic coverage for the unified staged harness.

Everything here runs WITHOUT a browser, an LLM, a DB, or the network: the harness
folds an injected ``GauntletVerdict`` and a mock observation, and the capture seam
is a no-op. That is the contract — the manifest scaffold ships money-free and is
ratcheted today; only the stream behind the two seams is paid.

Each defect class carries an adversary case that MUST flip its stage (and the
unified verdict) to FAIL — proving the manifest has teeth and is not a vacuous
green (the same discipline as the beauty gates 5/5 · 7/5).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# The manifest lives in scripts/ (the brief's literal path), not src/.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import e2e_manifest as em  # noqa: E402
import niche_batch as nb  # noqa: E402

from omnia_api.services import accept_gauntlet, reference_corpus  # noqa: E402

# ── stage taxonomy is well-formed and anchored to the real gate universe ──────


def _live_gate_universe() -> set[str]:
    """The gate ids ``accept_gauntlet.run()`` actually fans, derived LIVE.

    Every BOOLEAN dial on ``run()``'s signature is introspected and turned on, then
    run is driven over an empty file set with no target: each pure context-scan gate
    fires (every one is INERT-passing on an empty/``None`` context) while NO rendered
    leg fans (no ``index.html`` / url → ``render_expected`` is False). Money-free by
    construction — 0 LLM, 0 browser. A gate added to ``run()`` behind ANY dial
    therefore shows up here automatically, so it physically cannot be silently
    dropped from ``EXPECTED_GATES``. The rendered legs need a real target to fan, so
    they are anchored to their own ``RENDERED_GATES`` constant (un-orphanable — the
    same tuple is spread into ``EXPECTED_GATES``).
    """
    import asyncio
    import inspect

    bool_dials = {
        name: True
        for name, p in inspect.signature(accept_gauntlet.run).parameters.items()
        if isinstance(p.default, bool)
    }
    verdict = asyncio.run(accept_gauntlet.run(files={}, **bool_dials))
    return {g.gate for g in verdict.gates} | set(accept_gauntlet.RENDERED_GATES)


def test_expected_gates_cover_the_full_accept_gauntlet_universe() -> None:
    """De-orphan guard: ``EXPECTED_GATES`` == the live gate universe ``run()`` fans.

    Derived from a real ``accept_gauntlet.run()`` drive (every dial on, money-free)
    rather than a hand-maintained literal — so a gate added to the gauntlet but
    forgotten here turns this RED instead of being silently never checked. That
    silent orphan is exactly the failure this keystone exists to prevent, and it had
    itself happened: ``onboarding`` (V2.7), ``render`` (V3.12) and ``edit`` (V1.11)
    all fanned in ``run()`` yet were absent from ``EXPECTED_GATES``.
    """
    assert set(em.EXPECTED_GATES) == _live_gate_universe()
    # No duplicates in the ordered tuple.
    assert len(em.EXPECTED_GATES) == len(set(em.EXPECTED_GATES))


def test_stage_keys_are_unique_across_kinds() -> None:
    all_stages = list(em.EXPECTED_GATES) + list(em.OBSERVATION_STAGES)
    assert len(all_stages) == len(set(all_stages))


# ── the money-free mock stream is a clean PASS ────────────────────────────────


def test_mock_corpus_passes_unified_and_clears_the_wow_floor() -> None:
    verdict = em.run_manifest(em.mock_corpus())
    assert verdict.passed
    assert len(verdict.niches) == len(nb.CORPUS)
    for n in verdict.niches:
        assert n.passed()
        assert n.wow_score >= em.DEFAULT_WOW_FLOOR
        assert not n.hard_failed
        assert not n.abstained
        assert not n.coverage_gaps


def test_every_stage_gets_a_screenshot_and_each_niche_a_video() -> None:
    verdict = em.run_manifest(em.mock_corpus(count=1))
    n = verdict.niches[0]
    assert n.video_ref and n.video_ref.endswith(".video")
    for s in n.stages:
        assert s.capture_ref and s.capture_ref.startswith("capture://")
    # one stage per gate + per observation stage
    assert len(n.stages) == len(em.EXPECTED_GATES) + len(em.OBSERVATION_STAGES)


# ── adversary: each defect class flips its stage AND the unified verdict ───────


@pytest.mark.parametrize(
    "defect",
    [
        em.NARRATION,
        em.SWATCHES,
        em.JOY,
        em.PARAM_INHERIT,
        em.FORK_LINEAGE,
        accept_gauntlet.WOW_DOM,
        accept_gauntlet.TASTE,
        accept_gauntlet.VIRAL,
        accept_gauntlet.COMPOSE,
        accept_gauntlet.ONBOARDING,
        accept_gauntlet.RENDER,
        accept_gauntlet.EDIT,
    ],
)
def test_injected_defect_hard_fails_its_stage_and_the_run(defect: str) -> None:
    obs = em.mock_observation(nb.CORPUS[0], defects=[defect])
    verdict = em.run_manifest([obs])
    assert not verdict.passed
    niche = verdict.niches[0]
    assert not niche.passed()
    failed = {s.stage for s in niche.hard_failed}
    assert defect in failed
    assert niche in verdict.hard_failed


# ── coverage gap: a dropped gate abstains and sinks the strict pass ───────────


def test_dropped_gate_is_a_coverage_gap_abstain_not_a_pass() -> None:
    obs = em.ManifestObservation(
        niche="gap",
        prompt="p",
        gauntlet=em.synthetic_verdict(drop=[accept_gauntlet.REFERENCE]),
        is_fork=False,
        narration_present=True,
        swatches=("#fff",),
        joy_fired=True,
    )
    verdict = em.run_manifest([obs])
    niche = verdict.niches[0]
    assert accept_gauntlet.REFERENCE in niche.coverage_gaps
    ref = next(s for s in niche.stages if s.stage == accept_gauntlet.REFERENCE)
    assert ref.abstained and not ref.passed and not ref.hard_failed
    assert not niche.passed()  # no evidence ≠ a pass
    assert not verdict.passed


def test_none_gauntlet_abstains_every_gate_stage() -> None:
    obs = em.ManifestObservation(niche="empty", prompt="p", gauntlet=None)
    niche = em.run_manifest([obs]).niches[0]
    gate_stages = [s for s in niche.stages if s.kind == em.GATE_KIND]
    assert gate_stages and all(s.abstained for s in gate_stages)
    assert len(niche.coverage_gaps) == len(em.EXPECTED_GATES)


# ── INERT fork stages on a root niche; abstain on uncaptured fields ───────────


def test_root_niche_inerts_the_two_fork_stages() -> None:
    obs = em.ManifestObservation(
        niche="root",
        prompt="p",
        gauntlet=em.synthetic_verdict(),
        is_fork=False,
        narration_present=True,
        swatches=("#fff",),
        joy_fired=True,
        # no inherited_* / fork_lineage — must NOT matter for a root
    )
    niche = em.run_manifest([obs]).niches[0]
    for key in (em.PARAM_INHERIT, em.FORK_LINEAGE):
        s = next(x for x in niche.stages if x.stage == key)
        assert s.passed and not s.abstained
    assert niche.passed()


def test_uncaptured_observation_field_abstains_not_hard_fails() -> None:
    obs = em.ManifestObservation(
        niche="partial",
        prompt="p",
        gauntlet=em.synthetic_verdict(),
        is_fork=False,
        narration_present=None,  # not captured yet
        swatches=("#fff",),
        joy_fired=True,
    )
    niche = em.run_manifest([obs]).niches[0]
    narr = next(s for s in niche.stages if s.stage == em.NARRATION)
    assert narr.abstained and not narr.passed and not narr.hard_failed
    assert not niche.passed()


# ── V4.9 viral-eligible: the derived pillar-1 → pillar-4 bridge stage ──────────


def test_viral_eligible_stage_is_present_once_in_every_niche_row() -> None:
    verdict = em.run_manifest(em.mock_corpus())
    for n in verdict.niches:
        ve = [s for s in n.stages if s.stage == em.VIRAL_ELIGIBLE]
        assert len(ve) == 1
        assert ve[0].kind == em.OBSERVATION_KIND


def test_viral_eligible_passes_on_a_floor_green_verdict() -> None:
    s = em.viral_eligible_stage(em.synthetic_verdict())
    assert s.passed and not s.abstained and not s.hard_failed


def test_viral_eligible_abstains_when_no_gauntlet_captured() -> None:
    s = em.viral_eligible_stage(None)
    assert s.abstained and not s.passed and not s.hard_failed


@pytest.mark.parametrize("floor_leg", [accept_gauntlet.TASTE, accept_gauntlet.HIERARCHY])
def test_viral_eligible_hard_fails_on_a_real_floor_finding(floor_leg: str) -> None:
    # A measured pillar-1 defect (taste/hierarchy carries a finding) → a real
    # disqualification, not an abstain.
    s = em.viral_eligible_stage(em.synthetic_verdict(fail=[floor_leg]))
    assert s.hard_failed and not s.passed and not s.abstained
    assert "not-viral-eligible" in s.classes


@pytest.mark.parametrize("floor_leg", [accept_gauntlet.TASTE, accept_gauntlet.HIERARCHY])
def test_viral_eligible_abstains_on_an_unproven_floor(floor_leg: str) -> None:
    # A flaky / uncaptured floor leg cannot vouch — but it is not a defect.
    s = em.viral_eligible_stage(em.synthetic_verdict(abstain=[floor_leg]))
    assert s.abstained and not s.passed and not s.hard_failed


def test_viral_eligible_hard_fails_when_a_nonfloor_gate_has_a_finding() -> None:
    # Floor is green but another gate carries a real finding → ineligible, and a
    # real finding hard-fails (no door to virality for a defective surface).
    s = em.viral_eligible_stage(em.synthetic_verdict(fail=[accept_gauntlet.WOW_DOM]))
    assert s.hard_failed and not s.passed and not s.abstained


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"fail": [accept_gauntlet.TASTE]},
        {"fail": [accept_gauntlet.WOW_DOM]},
        {"abstain": [accept_gauntlet.HIERARCHY]},
        {"fail": [accept_gauntlet.VIRAL]},
    ],
)
def test_viral_eligible_stage_parity_with_production_predicate(kwargs: dict) -> None:
    """The stage passes iff the production V4.9 predicate vouches — R-04 fidelity."""
    verdict = em.synthetic_verdict(**kwargs)
    s = em.viral_eligible_stage(verdict)
    assert s.passed == accept_gauntlet.viral_eligible_from_verdict(verdict)


def test_floor_defect_sinks_both_its_gate_and_the_viral_bridge() -> None:
    # End-to-end: a taste defect hard-fails the taste gate AND the derived bridge,
    # and the unified run fails — the bridge never masks a real floor defect.
    obs = em.mock_observation(nb.CORPUS[0], defects=[accept_gauntlet.TASTE])
    niche = em.run_manifest([obs]).niches[0]
    failed = {s.stage for s in niche.hard_failed}
    assert accept_gauntlet.TASTE in failed
    assert em.VIRAL_ELIGIBLE in failed


# ── capture seam is driven once per stage + once per niche video ──────────────


def test_capture_seam_is_called_for_every_stage_and_video() -> None:
    calls: list[em.CaptureRequest] = []

    def recorder(req: em.CaptureRequest) -> str:
        calls.append(req)
        return f"rec://{req.niche}/{req.stage}/{req.kind}"

    em.run_manifest(em.mock_corpus(count=1), capture=recorder)
    stage_calls = [c for c in calls if c.kind == "screenshot"]
    video_calls = [c for c in calls if c.kind == "video"]
    assert len(stage_calls) == len(em.EXPECTED_GATES) + len(em.OBSERVATION_STAGES)
    assert len(video_calls) == 1


# ── security: target guard + ref sanitiser (the pre-owner-run audit) ──────────


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "file:///etc/passwd",
        "data:text/html,<script>",
        "javascript:alert(1)",
        "gopher://host/x",
        "ftp://host/x",
        "https://user:token@host/p",  # credential leak
        "https://host/ a",  # whitespace
        "http://host/\n",  # control char
    ],
)
def test_assert_safe_target_rejects_dangerous_urls(bad: str) -> None:
    with pytest.raises(ValueError):
        em.assert_safe_target(bad)


@pytest.mark.parametrize(
    "ok",
    [
        "https://constructor.lead-generator.ru/p/slug",
        "http://omnia-dev-sushi:3000/",
    ],
)
def test_assert_safe_target_accepts_clean_http_urls(ok: str) -> None:
    assert em.assert_safe_target(ok) == ok


def test_fold_niche_validates_a_present_url() -> None:
    obs = em.ManifestObservation(niche="x", prompt="p", gauntlet=em.synthetic_verdict(), url="file:///x")
    with pytest.raises(ValueError):
        em.fold_niche(obs)


@pytest.mark.parametrize(
    "raw,expect_no_traversal",
    [
        ("../../etc", True),
        ("a/b/c", True),
        ("..", True),
        ("Sushi Доставка", True),
        ("", True),
    ],
)
def test_safe_ref_never_traverses(raw: str, expect_no_traversal: bool) -> None:
    ref = em._safe_ref(raw)
    assert "/" not in ref
    assert ".." not in ref
    assert ref  # never empty


# ── unified subscore is JSON-serialisable (the artefact the owner-run emits) ──


def test_subscore_round_trips_through_json() -> None:
    verdict = em.run_manifest(em.mock_corpus(count=2))
    blob = json.dumps(verdict.subscore(), ensure_ascii=False)
    back = json.loads(blob)
    assert back["manifest"] == "paid-run"
    assert back["passed"] is True
    assert back["niches_run"] == 2
    assert len(back["niches"]) == 2
    assert back["niches"][0]["stages"]


def test_wow_score_reflects_passed_fraction() -> None:
    # one hard fail among the full stage set (every gate + every observation) →
    # (total-1)/total mapped onto the 0–10 rubric.
    obs = em.mock_observation(nb.CORPUS[0], defects=[em.JOY])
    niche = em.run_manifest([obs]).niches[0]
    total = len(em.EXPECTED_GATES) + len(em.OBSERVATION_STAGES)
    assert niche.wow_score == round(10.0 * (total - 1) / total, 1)


def test_empty_run_is_not_a_pass() -> None:
    assert not em.run_manifest([]).passed


# ── V-MANIFEST-FROZEN: the deterministic dress-rehearsal over real static HTML ─
#
# These run WITHOUT a browser or an LLM: the frozen mode folds the manifest over
# the four committed corpus snapshots, deriving every verdict from a real static
# read of the real HTML. The mock tests above prove the fold; these prove it
# reads the actual corpus and has teeth when a file is defaced.

_HEX_SUB = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _corpus() -> dict[str, str]:
    corpus = reference_corpus.load_corpus()
    assert corpus, "frozen corpus must be committed under reference_corpus_data/"
    return corpus


def _agency() -> str:
    return _corpus()["agency"]


def test_frozen_corpus_passes_unified_and_clears_the_wow_floor() -> None:
    """Every committed enterprise snapshot clears every folded proof, WOW≥8."""
    verdict = em.run_manifest(em.frozen_corpus())
    assert verdict.passed
    assert len(verdict.niches) == len(_corpus())
    for n in verdict.niches:
        assert n.passed()
        assert n.wow_score >= em.DEFAULT_WOW_FLOOR
        assert not n.hard_failed
        assert not n.abstained
        assert not n.coverage_gaps  # every gate accounted for over the static page


def test_frozen_verdict_covers_exactly_the_expected_gate_universe() -> None:
    """A frozen verdict carries every EXPECTED gate (no coverage gap, no extras)."""
    verdict = em.frozen_verdict(_agency())
    gates = {g.gate for g in verdict.gates}
    assert gates == set(em.EXPECTED_GATES)


def test_frozen_real_gates_are_genuine_not_surrogate() -> None:
    """defect/compose/viral/onboarding/render/edit are the REAL browser-free gates.

    Their subscore is the gauntlet's own shape, never tagged ``frozen`` — only the
    rendered-leg stand-ins carry that provenance marker.
    """
    by_gate = {g.gate: g for g in em.frozen_verdict(_agency()).gates}
    real = (
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.COMPOSE,
        accept_gauntlet.VIRAL,
        accept_gauntlet.ONBOARDING,
        accept_gauntlet.RENDER,
        accept_gauntlet.EDIT,
    )
    for gate in real:
        assert not by_gate[gate].subscore.get("frozen"), gate


def test_frozen_rendered_legs_are_tagged_as_static_surrogates() -> None:
    """Every rendered leg is explicitly a frozen stand-in (provenance, R-10)."""
    by_gate = {g.gate: g for g in em.frozen_verdict(_agency()).gates}
    for gate in accept_gauntlet.RENDERED_GATES:
        gv = by_gate[gate]
        assert gv.subscore.get("frozen") is True, gate
        assert gv.summary.startswith("frozen-static:"), gate
        assert not gv.abstained  # a static page is always present → never abstains


def test_frozen_swatches_are_the_pages_real_palette() -> None:
    """The surfaced swatches are the page's actual hex palette (V3.4 ≥3 floor)."""
    obs = em.frozen_observation("agency", "p", _agency())
    assert obs.swatches and len(obs.swatches) >= em.MIN_SWATCHES
    assert all(s.startswith("#") for s in obs.swatches)
    # and they really come from the document
    in_html = {m.group(0).lower() for m in _HEX_SUB.finditer(_agency())}
    assert set(obs.swatches) <= in_html


# Adversary defacements of a REAL corpus file — each must flip the unified verdict
# and hard-fail the gate it targets. (name, transform, expected hard-failed gate.)
def _flatten(_: str) -> str:
    # A catastrophically flat page: one type size, no sections, no hero — but a
    # valid a11y shell, so the failure is isolated to the composition floor.
    return (
        '<html lang="ru"><head><title>x</title>'
        '<meta name="viewport" content="width=device-width"></head>'
        "<body><p>hello</p></body></html>"
    )


def _blank_palette(html: str) -> str:
    return _HEX_SUB.sub("#000000", html)


def _inject_dead_auth(html: str) -> str:
    cta = '<a href="/">Войти</a>'
    return html.replace("</body>", cta + "</body>") if "</body>" in html else html + cta


def _strip_lang(html: str) -> str:
    return re.sub(r'\blang\s*=\s*"[^"]*"', "", html, flags=re.IGNORECASE)


@pytest.mark.parametrize(
    "deface,expected_gate",
    [
        (_flatten, accept_gauntlet.COMPOSE),
        (_flatten, accept_gauntlet.TASTE),
        (_flatten, accept_gauntlet.HIERARCHY),
        (_blank_palette, accept_gauntlet.WOW_DOM),
        (_blank_palette, em.SWATCHES),
        (_inject_dead_auth, accept_gauntlet.DEFECT_REGISTRY),
        (_strip_lang, accept_gauntlet.PERF_A11Y),
    ],
)
def test_frozen_adversary_defacement_flips_the_run(deface, expected_gate) -> None:
    bad_html = deface(_agency())
    verdict = em.run_manifest([em.frozen_observation("agency", "p", bad_html)])
    assert not verdict.passed
    niche = verdict.niches[0]
    assert not niche.passed()
    assert expected_gate in {s.stage for s in niche.hard_failed}
    assert niche in verdict.hard_failed


def test_frozen_data_leg_is_inert_on_a_landing_page() -> None:
    """A landing page with no data surface is not a data defect (mirrors live)."""
    by_gate = {g.gate: g for g in em.frozen_verdict(_agency()).gates}
    data = by_gate[accept_gauntlet.DATA]
    assert data.passed and not data.classes


def test_frozen_observation_is_a_root_niche() -> None:
    """A corpus page is a root: the two fork stages stay INERT (not faked)."""
    niche = em.run_manifest([em.frozen_observation("agency", "p", _agency())]).niches[0]
    for key in (em.PARAM_INHERIT, em.FORK_LINEAGE):
        s = next(x for x in niche.stages if x.stage == key)
        assert s.passed and not s.abstained


def test_frozen_subscore_round_trips_through_json() -> None:
    verdict = em.run_manifest(em.frozen_corpus())
    back = json.loads(json.dumps(verdict.subscore(), ensure_ascii=False))
    assert back["passed"] is True
    assert back["niches_run"] == len(_corpus())
