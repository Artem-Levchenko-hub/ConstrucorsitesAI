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
import sys
from pathlib import Path

import pytest

# The manifest lives in scripts/ (the brief's literal path), not src/.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import e2e_manifest as em  # noqa: E402
import niche_batch as nb  # noqa: E402

from omnia_api.services import accept_gauntlet  # noqa: E402

# ── stage taxonomy is well-formed and anchored to the real gate universe ──────


def test_expected_gates_cover_the_full_accept_gauntlet_universe() -> None:
    """De-orphan guard: every real gate id is represented exactly once.

    If a gate is added to ``accept_gauntlet`` it must appear here or the manifest
    would silently never check it — that is exactly the orphan failure this
    keystone exists to prevent.
    """
    expected = set(em.EXPECTED_GATES)
    real = {
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.COMPOSE,
        accept_gauntlet.VIRAL,
        *accept_gauntlet.RENDERED_GATES,
    }
    assert expected == real
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
    # one hard fail among (10 gates + 5 obs) = 14/15 passed → 9.3/10
    obs = em.mock_observation(nb.CORPUS[0], defects=[em.JOY])
    niche = em.run_manifest([obs]).niches[0]
    total = len(em.EXPECTED_GATES) + len(em.OBSERVATION_STAGES)
    assert niche.wow_score == round(10.0 * (total - 1) / total, 1)


def test_empty_run_is_not_a_pass() -> None:
    assert not em.run_manifest([]).passed
