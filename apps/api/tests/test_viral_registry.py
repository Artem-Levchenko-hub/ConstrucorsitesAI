"""V4.6 — the viral defect-registry ratchet.

Every viral class carries an adversary fixture that MUST fail its assert (like the
beauty gates 5/5 · 7/5), proving the gate has teeth and is not a vacuous green. A
clean episode passes, an empty context is INERT, and the registry is wired into
the acceptance gauntlet through the ``viral=`` dial (it is not an orphan). Pure
Python — no browser, no LLM, no DB.
"""

from __future__ import annotations

from omnia_api.services import accept_gauntlet, first_paint_gate, viral_registry
from omnia_api.services.viral_registry import ViralContext, scan

# ── shared building blocks ──────────────────────────────────────────────────────


def _healthy_obs() -> first_paint_gate.Obs:
    """A served-surface observation a cold stranger lands on alive."""
    return {
        "url": "https://constructor.lead-generator.ru/p/demo-slug",
        "text_count": 42,
        "rows": 12,
        "hero_visible": True,
        "cta_visible": True,
        "first_paint_ms": 800,
    }


def _clean_ctx(**over: object) -> ViralContext:
    """A fully-described, regression-free share→fork episode."""
    base: dict[str, object] = dict(
        phase="done",
        served_live=True,
        remix_cta_present=True,
        served_observation=_healthy_obs(),
        source_project_id="proj-src",
        fork_project_id="proj-fork",
        source_design_preset_id="clinic-dark",
        fork_design_preset_id="clinic-dark",
        source_discovery_spec={"tone": "calm", "focal_hero": "booking"},
        fork_discovery_spec={"tone": "calm", "focal_hero": "booking"},
    )
    base.update(over)
    return ViralContext(**base)  # type: ignore[arg-type]


# ── the clean / inert baselines ─────────────────────────────────────────────────


def test_clean_episode_passes() -> None:
    rep = scan(_clean_ctx())
    assert rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_none_context_is_inert() -> None:
    rep = scan(None)
    assert not rep.judged
    assert rep.passed  # INERT — nothing to judge ≠ a failure
    assert rep.classes == ()


def test_empty_context_is_inert() -> None:
    rep = scan(ViralContext())
    assert not rep.judged
    assert rep.passed
    assert rep.classes == ()


# ── one adversary fixture PER class — each MUST fail its assert ──────────────────


def test_adversary_link_served_before_done() -> None:
    rep = scan(_clean_ctx(phase="building", served_live=True))
    assert not rep.passed
    assert viral_registry.LINK_BEFORE_DONE in rep.classes


def test_phase_building_without_live_surface_is_clean() -> None:
    # The tasteful "building" stub (served_live=False) is the CORRECT behaviour.
    rep = scan(_clean_ctx(phase="building", served_live=False))
    assert rep.passed
    assert viral_registry.LINK_BEFORE_DONE not in rep.classes


def test_adversary_auth_wall_folds_first_paint() -> None:
    obs = _healthy_obs()
    obs["url"] = "https://constructor.lead-generator.ru/login?next=/p/demo"
    rep = scan(_clean_ctx(served_observation=obs))
    assert not rep.passed
    assert first_paint_gate.AUTH_WALL in rep.classes


def test_adversary_empty_shell_folds_first_paint() -> None:
    obs = _healthy_obs()
    obs["text_count"] = 1  # ≤ SHELL_TEXT_FLOOR → a placeholder shell
    rep = scan(_clean_ctx(served_observation=obs))
    assert not rep.passed
    assert first_paint_gate.EMPTY_SHELL in rep.classes


def test_adversary_slow_first_paint_folds_first_paint() -> None:
    obs = _healthy_obs()
    obs["first_paint_ms"] = first_paint_gate.FIRST_PAINT_BUDGET_MS + 5_000
    rep = scan(_clean_ctx(served_observation=obs))
    assert not rep.passed
    assert first_paint_gate.SLOW_FIRST_PAINT in rep.classes


def test_adversary_dead_remix_cta() -> None:
    rep = scan(_clean_ctx(remix_cta_present=False))
    assert not rep.passed
    assert viral_registry.DEAD_REMIX_CTA in rep.classes


def test_adversary_leaked_tenant_fork_same_id() -> None:
    rep = scan(_clean_ctx(fork_project_id="proj-src"))  # identical to source
    assert not rep.passed
    assert viral_registry.LEAKED_TENANT_FORK in rep.classes


def test_adversary_dropped_seed_preset() -> None:
    rep = scan(_clean_ctx(fork_design_preset_id=None))  # niche dropped on fork
    assert not rep.passed
    assert viral_registry.DROPPED_SEED_PARAM in rep.classes


def test_adversary_dropped_seed_discovery_spec() -> None:
    rep = scan(_clean_ctx(fork_discovery_spec={}))  # source had answers, fork blank
    assert not rep.passed
    assert viral_registry.DROPPED_SEED_PARAM in rep.classes


# ── segment independence + invariants ───────────────────────────────────────────


def test_fork_only_context_judges_fork_segment_only() -> None:
    # No served-surface inputs → only the fork segment is judged.
    rep = scan(
        ViralContext(
            source_project_id="proj-src",
            fork_project_id="proj-src",  # leak
        )
    )
    assert rep.judged
    assert not rep.passed
    assert rep.classes == (viral_registry.LEAKED_TENANT_FORK,)


def test_classes_are_in_canonical_order() -> None:
    # Two regressions out of canonical order in input still report in order.
    obs = _healthy_obs()
    obs["text_count"] = 1  # empty-shell (earlier in VIRAL_CLASSES)
    rep = scan(_clean_ctx(served_observation=obs, fork_project_id="proj-src"))
    assert rep.classes == (
        first_paint_gate.EMPTY_SHELL,
        viral_registry.LEAKED_TENANT_FORK,
    )


def test_source_without_seed_never_flags_dropped_param() -> None:
    # A source that itself carried no niche cannot "drop" one onto the fork.
    rep = scan(
        _clean_ctx(
            source_design_preset_id=None,
            source_discovery_spec=None,
            fork_design_preset_id=None,
            fork_discovery_spec=None,
        )
    )
    assert rep.passed
    assert viral_registry.DROPPED_SEED_PARAM not in rep.classes


def test_subscore_shape() -> None:
    rep = scan(_clean_ctx(remix_cta_present=False))
    sub = rep.subscore()
    assert sub["gate"] == "viral"
    assert sub["passed"] is False
    assert sub["judged"] is True
    assert viral_registry.DEAD_REMIX_CTA in sub["classes"]
    assert sub["count"] >= 1


# ── the registry is WIRED into the gauntlet (not an orphan) ──────────────────────


async def test_gauntlet_viral_dial_off_by_default() -> None:
    # No viral dial, no files → nothing viral in the verdict.
    verdict = await accept_gauntlet.run(viral_context=_clean_ctx())
    assert accept_gauntlet.VIRAL not in [g.gate for g in verdict.gates]


async def test_gauntlet_viral_dial_passes_clean_episode() -> None:
    verdict = await accept_gauntlet.run(viral=True, viral_context=_clean_ctx())
    viral = next(g for g in verdict.gates if g.gate == accept_gauntlet.VIRAL)
    assert viral.passed
    assert not viral.abstained


async def test_gauntlet_viral_dial_hard_fails_on_regression() -> None:
    ctx = _clean_ctx(remix_cta_present=False)
    verdict = await accept_gauntlet.run(viral=True, viral_context=ctx)
    viral = next(g for g in verdict.gates if g.gate == accept_gauntlet.VIRAL)
    assert not viral.passed
    assert viral in verdict.hard_failed  # a real finding blocks ship, not an abstain
    assert f"{accept_gauntlet.VIRAL}:{viral_registry.DEAD_REMIX_CTA}" in (
        verdict.failed_classes
    )


async def test_gauntlet_viral_dial_inert_on_empty_context() -> None:
    # viral=True but no context → INERT pass, not an abstain or a hard fail.
    verdict = await accept_gauntlet.run(viral=True)
    viral = next(g for g in verdict.gates if g.gate == accept_gauntlet.VIRAL)
    assert viral.passed
    assert not viral.abstained
    assert viral not in verdict.hard_failed
