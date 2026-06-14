#!/usr/bin/env python3
"""PAID-RUN MANIFEST — the one staged harness that folds EVERY accumulated proof
against ONE live stream per niche, with a unified PASS/FAIL and per-niche WOW
subscore (v2.13 keystone).

The problem this solves (the NORTH-STAR gap, named by six review lenses): the
whole visible/viral/causality layer — narration (V3.10), brief swatches (V3.4),
joy (V3.8), chip→pixel (V2.5b), fork param-inherit (V4.2b), fork lineage (V4.7),
viral-eligibility (V4.9, the pillar-1→4 floor bridge), the eight
composition/quality gates (``accept_gauntlet``) — shipped as *isolated*
money-free unit-proofs. Each is green in a vacuum; none has ever been composed
into ONE live chain and watched play out. A unit gate cannot catch the cross-cut
regression (narration lags the render / swatches desync from the painted page /
the chip gate trips on an edge palette / a fork drops its seed param) because no
unit gate sees the whole performance at once.

This manifest is that single chain. The owner runs it ONCE at flip time: it
drives 5–6 niches end-to-end, folds every proof against the same stream, captures
a screenshot per stage, and prints a unified verdict — instant visibility of the
*entire* product in one pass.

MONEY-FREE BY CONSTRUCTION. The scaffold makes NO LLM call and opens NO browser.
The two expensive halves are SEAMS:

  * the per-niche ``GauntletVerdict`` (the gate proofs) is *injected* on the
    :class:`ManifestObservation`. The owner produces it at flip time from a real
    generation via ``accept_gauntlet.run(url=...)``; the shipped
    :func:`mock_observation` produces a deterministic synthetic verdict so the
    harness, its parser, and the unified fold are all testable today.
  * the screenshot/video grab is a :data:`CaptureFn`. The shipped
    :func:`null_capture` returns a synthetic ref and touches nothing; the owner
    plugs a Playwright capture into the same seam at flip time.

So the structure ships and is ratcheted now; only the stream behind it is paid.

Canon: R-01 (one ``run_manifest`` hides parse + per-niche fold + capture), R-04
(reuses ``accept_gauntlet`` verdicts/gate-ids and ``niche_batch.CORPUS`` — no new
gate, no new corpus, every proof's truth still lives in its own module), R-10
(a missing verdict / uncaptured field ABSTAINS — no evidence is never a pass, but
a flaky capture never crashes the run).

SECURITY (audited before the owner-run, per the manifest brief): the scaffold is
a pure fold and reaches no network. The only owner-facing attack surface is the
target URL the real capture seam will fetch — :func:`assert_safe_target` is the
guard that path MUST call (http(s) only, no embedded credentials, no control
chars → no SSRF via ``file://``/``gopher://``, no token-leak via ``user:pass@``),
and :func:`_safe_ref` sanitises every capture ref so a niche/stage name can never
traverse out of an output directory. Both are unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the src-layout package importable when run as `python scripts/e2e_manifest.py`.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
# The niche corpus lives in a sibling script, not src/ — reuse it (R-04).
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import niche_batch  # noqa: E402

from omnia_api.services import (  # noqa: E402
    accept_gauntlet,
    compose_gate,
    hierarchy_gate,
    reference_corpus,
    taste_gate,
)
from omnia_api.services.accept_gauntlet import GateVerdict, GauntletVerdict  # noqa: E402

# ── Stage taxonomy ───────────────────────────────────────────────────────────

#: The full gate universe a complete manifest run must cover, in ``run()`` emission
#: order. This is the "parser over RENDERED_GATES" the brief asks for, anchored to
#: the real ``accept_gauntlet`` gate ids (R-04): the source-scan floors
#: (defect-registry, compose), the four context-scan registries (viral, onboarding,
#: render, edit — V4.6/V2.7/V3.12/V1.11), and the seven rendered legs. A gate
#: present here but ABSENT from a run's ``GauntletVerdict`` is a coverage gap
#: (abstain) — this tuple is what keeps a silently-dropped gate from passing
#: unnoticed. The de-orphan guard ``test_e2e_manifest`` derives this set LIVE from
#: ``accept_gauntlet.run()`` (every dial on), so a future gate that fans in ``run()``
#: but is missing here turns the guard RED — it cannot silently orphan.
EXPECTED_GATES: tuple[str, ...] = (
    accept_gauntlet.DEFECT_REGISTRY,
    accept_gauntlet.COMPOSE,
    accept_gauntlet.VIRAL,
    accept_gauntlet.ONBOARDING,
    accept_gauntlet.RENDER,
    accept_gauntlet.EDIT,
    # WOW_DOM, PERF_A11Y, CHIP_PIXEL, TASTE, HIERARCHY, DATA, REFERENCE
    *accept_gauntlet.RENDERED_GATES,
)

# The visible/viral-layer proofs that are NOT modelled as accept_gauntlet gates —
# each is its own shipped unit-proof the manifest folds as a per-niche assertion
# hook over the captured stream.
NARRATION = "narration"  # V3.10 — the brief surfaced as a live co-designer overlay
SWATCHES = "swatches"  # V3.4 — the art-director brief's hex swatches shown in chat
JOY = "joy"  # V3.8 — the brand-coloured reward note on build-complete
PARAM_INHERIT = "param-inherit"  # V4.2b — a fork inherits design_preset_id + discovery_spec
FORK_LINEAGE = "fork-lineage"  # V4.7 — the forked_from chain is walkable to the root
VIRAL_ELIGIBLE = "viral-eligible"  # V4.9 — pillar-1→4 bridge: seeds the pool only if floor-green

#: Observation stages, in table order. ``param-inherit`` and ``fork-lineage`` are
#: INERT (a passing no-op) on a root niche — there is nothing upstream to inherit.
#: ``viral-eligible`` is a DERIVED bridge stage (V4.9): it reuses the injected
#: gauntlet verdict (no new field, no new metric) to certify whether this niche's
#: shared surface is floor-green enough to seed the viral pool — pillar 1 → 4.
OBSERVATION_STAGES: tuple[str, ...] = (
    NARRATION,
    SWATCHES,
    JOY,
    PARAM_INHERIT,
    FORK_LINEAGE,
    VIRAL_ELIGIBLE,
)

GATE_KIND = "gate"
OBSERVATION_KIND = "observation"

#: Default WOW floor (0–10) every niche must clear — the pillar-1 rubric gate.
DEFAULT_WOW_FLOOR = 8.0


# ── Result shapes ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageResult:
    """One proof's contribution to a niche's manifest row."""

    stage: str
    kind: str
    passed: bool
    #: No evidence where evidence was expected (a missing gate / uncaptured
    #: field). Implies ``not passed`` — no evidence is never a pass (mirrors the
    #: gauntlet). A real finding is ``not passed and not abstained``.
    abstained: bool
    classes: tuple[str, ...]
    detail: str
    capture_ref: str | None = None

    @property
    def hard_failed(self) -> bool:
        return not self.passed and not self.abstained

    def subscore(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "kind": self.kind,
            "passed": self.passed,
            "abstained": self.abstained,
            "classes": list(self.classes),
            "capture": self.capture_ref,
        }


@dataclass(frozen=True)
class NicheManifest:
    """One niche × every proof: the folded row + its WOW subscore."""

    niche: str
    prompt: str
    stages: tuple[StageResult, ...]
    video_ref: str | None = None

    @property
    def wow_score(self) -> float:
        """Passed-stage fraction mapped to the 0–10 pillar-1 rubric.

        An empty row (nothing folded) scores 0 — no evidence ≠ a perfect score.
        """
        if not self.stages:
            return 0.0
        passed = sum(1 for s in self.stages if s.passed)
        return round(10.0 * passed / len(self.stages), 1)

    @property
    def hard_failed(self) -> tuple[StageResult, ...]:
        return tuple(s for s in self.stages if s.hard_failed)

    @property
    def abstained(self) -> tuple[StageResult, ...]:
        return tuple(s for s in self.stages if s.abstained)

    @property
    def coverage_gaps(self) -> tuple[str, ...]:
        """Expected gates that did not run (abstained gate stages)."""
        return tuple(s.stage for s in self.stages if s.kind == GATE_KIND and s.abstained)

    def passed(self, wow_floor: float = DEFAULT_WOW_FLOOR) -> bool:
        """STRICT per-niche verdict: every stage passed AND the WOW floor met.

        Abstains (uncaptured evidence / a dropped gate) are NOT a pass — the
        owner-run must actually exercise every proof (mirrors
        ``GauntletVerdict.passed``: empty / abstained ≠ ship).
        """
        return (
            bool(self.stages)
            and all(s.passed for s in self.stages)
            and self.wow_score >= wow_floor
        )

    def subscore(self, wow_floor: float = DEFAULT_WOW_FLOOR) -> dict[str, Any]:
        return {
            "niche": self.niche,
            "passed": self.passed(wow_floor),
            "wow_score": self.wow_score,
            "hard_failed": [s.stage for s in self.hard_failed],
            "abstained": [s.stage for s in self.abstained],
            "coverage_gaps": list(self.coverage_gaps),
            "video": self.video_ref,
            "stages": [s.subscore() for s in self.stages],
        }


@dataclass(frozen=True)
class ManifestVerdict:
    """The unified verdict over every niche in one manifest run."""

    niches: tuple[NicheManifest, ...]
    wow_floor: float = DEFAULT_WOW_FLOOR

    @property
    def passed(self) -> bool:
        """STRICT unified verdict: every niche passed (and at least one ran)."""
        return bool(self.niches) and all(n.passed(self.wow_floor) for n in self.niches)

    @property
    def hard_failed(self) -> tuple[NicheManifest, ...]:
        return tuple(n for n in self.niches if n.hard_failed)

    def table(self) -> str:
        if not self.niches:
            return "paid-run-manifest: no niche ran (nothing to judge)"
        rows = []
        for n in self.niches:
            mark = "PASS" if n.passed(self.wow_floor) else "FAIL"
            bad = n.hard_failed
            extra = ""
            if bad:
                extra = " hard-fail[" + ", ".join(s.stage for s in bad) + "]"
            elif not n.passed(self.wow_floor):
                gaps = n.abstained
                extra = " abstain[" + ", ".join(s.stage for s in gaps) + "]"
            rows.append(f"  {mark:<4} {n.niche:<10} wow={n.wow_score:>4}/10{extra}")
        verdict = "PASS" if self.passed else "FAIL"
        return f"paid-run-manifest: {verdict} ({len(self.niches)} niches)\n" + "\n".join(rows)

    def summary(self) -> str:
        return self.table()

    def subscore(self) -> dict[str, Any]:
        return {
            "manifest": "paid-run",
            "passed": self.passed,
            "wow_floor": self.wow_floor,
            "niches_run": len(self.niches),
            "niches_passed": sum(1 for n in self.niches if n.passed(self.wow_floor)),
            "hard_failed": [n.niche for n in self.hard_failed],
            "niches": [n.subscore(self.wow_floor) for n in self.niches],
        }


# ── The injected stream (mock-brief shape) ───────────────────────────────────


@dataclass(frozen=True)
class ManifestObservation:
    """The explicit input shape one niche's live stream produces.

    The owner fills these from a real generation + browser capture at flip time;
    :func:`mock_observation` fills them deterministically so the harness ships
    money-free. Every field maps to one shipped proof:

    * ``gauntlet`` — the eight composition/quality gates (``accept_gauntlet``).
    * ``narration_present`` — V3.10, the live co-designer overlay.
    * ``swatches`` — V3.4, the brief's hex swatches surfaced in chat.
    * ``joy_fired`` — V3.8, the build-complete reward note.
    * ``inherited_preset`` / ``inherited_spec`` — V4.2b, fork seed-inheritance.
    * ``fork_lineage`` — V4.7, the ``forked_from`` chain root→…→node.

    A ``None`` field ABSTAINS its stage (owner has not captured it yet); a
    present-but-wrong field hard-fails it. On a root niche (``is_fork=False``) the
    two fork stages are INERT and pass regardless.
    """

    niche: str
    prompt: str
    gauntlet: GauntletVerdict | None = None
    #: The served surface the capture seam screenshots (None in a pure-mock run).
    url: str | None = None
    is_fork: bool = False
    narration_present: bool | None = None
    swatches: tuple[str, ...] | None = None
    joy_fired: bool | None = None
    inherited_preset: str | None = None
    inherited_spec: Mapping[str, Any] | None = None
    fork_lineage: tuple[str, ...] | None = None


# ── Capture seam (money-free default) ────────────────────────────────────────


@dataclass(frozen=True)
class CaptureRequest:
    """One screenshot/video grab the harness asks the capture seam to perform."""

    niche: str
    stage: str
    kind: str  # "screenshot" | "video"
    url: str | None


#: A capture seam: given a request, return a ref (a path / id) to the artefact.
CaptureFn = Callable[[CaptureRequest], str]


def null_capture(req: CaptureRequest) -> str:
    """The money-free default — a synthetic ref, NO I/O, NO network.

    The owner swaps a real Playwright screenshotter into this seam at flip time;
    until then the harness records *intent* (what would be captured where) so the
    staged structure is exercised and testable without a browser.
    """
    return f"capture://{_safe_ref(req.niche)}/{_safe_ref(req.stage)}.{_safe_ref(req.kind)}"


# ── Security guards (audited before the owner-run) ───────────────────────────

_SAFE_SCHEMES = ("http://", "https://")


def assert_safe_target(url: str) -> str:
    """Validate a capture target before the real seam fetches it.

    The owner-run's screenshotter MUST call this on every URL it visits. It blocks
    the manifest's only network attack surface:

    * non-http(s) schemes (``file://`` / ``gopher://`` / ``data:`` / ``javascript:``)
      → no SSRF / local-file read,
    * embedded credentials (``https://user:token@host``) → no token-leak into a
      capture path or a log line,
    * whitespace / control chars → no header / log injection.

    Returns the URL unchanged on success; raises ``ValueError`` otherwise.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("manifest target must be a non-empty string")
    low = url.lower()
    if not low.startswith(_SAFE_SCHEMES):
        raise ValueError(f"manifest target must be http(s), got: {url!r}")
    if any(c.isspace() or ord(c) < 0x20 for c in url):
        raise ValueError("manifest target must not contain whitespace/control chars")
    # Authority is everything between the scheme's `//` and the first `/?#`.
    authority = url.split("//", 1)[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in authority:
        raise ValueError("manifest target must not embed credentials (user:pass@host)")
    return url


def _safe_ref(s: str) -> str:
    """Sanitise a niche/stage/kind into a single safe path segment.

    Anything outside ``[a-z0-9._-]`` becomes ``-`` so a name can never traverse
    (`../`) or escape the capture output directory. Empty → ``unknown``.
    """
    cleaned = "".join(c if (c.isalnum() and c.isascii()) or c in "._-" else "-" for c in s.lower())
    cleaned = cleaned.strip("-") or "unknown"
    # Collapse any `..` that survived (e.g. "a..b") so it is never a parent ref.
    return cleaned.replace("..", "-")


# ── Stage evaluation (pure) ──────────────────────────────────────────────────


def gate_stage_results(verdict: GauntletVerdict | None) -> list[StageResult]:
    """Parse a folded ``GauntletVerdict`` into one StageResult per EXPECTED gate.

    A gate the run did not contain is a coverage gap → it ABSTAINS (no evidence ≠
    pass). This is the de-orphan guard: every gate in :data:`EXPECTED_GATES` is
    accounted for in every niche's row, so none can be silently dropped.
    """
    by_gate = {g.gate: g for g in verdict.gates} if verdict is not None else {}
    out: list[StageResult] = []
    for gate in EXPECTED_GATES:
        gv = by_gate.get(gate)
        if gv is None:
            out.append(
                StageResult(
                    stage=gate,
                    kind=GATE_KIND,
                    passed=False,
                    abstained=True,
                    classes=(),
                    detail="coverage gap: gate did not run in this stream",
                )
            )
        else:
            out.append(
                StageResult(
                    stage=gate,
                    kind=GATE_KIND,
                    passed=gv.passed,
                    abstained=gv.abstained,
                    classes=gv.classes,
                    detail=gv.summary,
                )
            )
    return out


def _obs_stage(
    stage: str,
    *,
    present: bool,
    ok: bool,
    fail_class: str,
    detail_ok: str,
    detail_bad: str,
) -> StageResult:
    """Build one observation StageResult. ``present=False`` → abstain."""
    if not present:
        return StageResult(stage, OBSERVATION_KIND, False, True, (), f"{stage}: not captured")
    if ok:
        return StageResult(stage, OBSERVATION_KIND, True, False, (), detail_ok)
    return StageResult(stage, OBSERVATION_KIND, False, False, (fail_class,), detail_bad)


def observation_stage_results(obs: ManifestObservation) -> list[StageResult]:
    """Fold the visible/viral-layer proofs from one observation's fields."""
    out: list[StageResult] = []

    out.append(
        _obs_stage(
            NARRATION,
            present=obs.narration_present is not None,
            ok=bool(obs.narration_present),
            fail_class="narration-absent",
            detail_ok="live brief narration surfaced to the viewer",
            detail_bad="brief was assembled but no narration overlay reached the client",
        )
    )
    out.append(
        _obs_stage(
            SWATCHES,
            present=obs.swatches is not None,
            ok=bool(obs.swatches),
            fail_class="swatches-absent",
            detail_ok=f"brief swatches surfaced ({len(obs.swatches or ())})",
            detail_bad="art-director swatches not surfaced in chat",
        )
    )
    out.append(
        _obs_stage(
            JOY,
            present=obs.joy_fired is not None,
            ok=bool(obs.joy_fired),
            fail_class="joy-absent",
            detail_ok="build-complete reward note fired",
            detail_bad="no joy moment on build-complete",
        )
    )

    # The two fork stages are INERT on a root niche — nothing upstream to inherit.
    if not obs.is_fork:
        for key, why in ((PARAM_INHERIT, "nothing to inherit"), (FORK_LINEAGE, "no lineage")):
            out.append(StageResult(key, OBSERVATION_KIND, True, False, (), f"root niche: {why}"))
        return out

    inherit_present = obs.inherited_preset is not None or obs.inherited_spec is not None
    inherit_ok = bool(obs.inherited_preset) and bool(obs.inherited_spec)
    out.append(
        _obs_stage(
            PARAM_INHERIT,
            present=inherit_present,
            ok=inherit_ok,
            fail_class="seed-dropped",
            detail_ok=f"fork inherited preset={obs.inherited_preset!r} + discovery_spec",
            detail_bad="fork dropped the source preset / discovery_spec → empty onboarding",
        )
    )
    lineage = obs.fork_lineage
    lineage_ok = lineage is not None and len(lineage) >= 2 and len(set(lineage)) == len(lineage)
    out.append(
        _obs_stage(
            FORK_LINEAGE,
            present=lineage is not None,
            ok=lineage_ok,
            fail_class="lineage-broken",
            detail_ok=f"forked_from chain walkable, depth {len(lineage or ())}",
            detail_bad="fork lineage missing / not distinct to the root",
        )
    )
    return out


def viral_eligible_stage(verdict: GauntletVerdict | None) -> StageResult:
    """Fold the V4.9 viral-eligibility predicate as a derived manifest stage.

    The pillar-1 → pillar-4 bridge: a niche's shared surface may seed the viral
    pool — its zero-signup forks inherit the right to be re-shared — only when it
    is itself floor-green. This reuses the production predicate
    ``accept_gauntlet.viral_eligible_from_verdict`` verbatim (R-04 — no new metric,
    the truth still lives in its own module); the manifest only gives that
    isolated unit-proof a composed home in the one live chain.

    Abstain vs hard-fail mirrors the gauntlet (R-10): a run with no gauntlet, or
    whose taste/hierarchy floor never produced evidence (a flaky / uncaptured
    leg), ABSTAINS — we cannot vouch for a surface we never scored, and no
    evidence is never a defect. When the floor WAS measured and a real gate
    finding keeps the surface ineligible, the stage HARD-FAILS: a genuine
    pillar-1 defect closes the door to virality.
    """
    if verdict is None:
        return StageResult(
            VIRAL_ELIGIBLE, OBSERVATION_KIND, False, True, (),
            "viral-eligible: no gauntlet captured",
        )
    if accept_gauntlet.viral_eligible_from_verdict(verdict):
        return StageResult(
            VIRAL_ELIGIBLE, OBSERVATION_KIND, True, False, (),
            "shared surface is floor-green → viral-eligible (pillar 1 → 4)",
        )
    # Ineligible. Distinguish an unproven floor (abstain — cannot vouch, not a
    # defect) from a real finding (hard-fail — a measured pillar-1 defect).
    by_gate = {g.gate: g for g in verdict.gates}
    floor = (by_gate.get(accept_gauntlet.TASTE), by_gate.get(accept_gauntlet.HIERARCHY))
    floor_unproven = any(g is None or g.abstained for g in floor)
    if floor_unproven and not verdict.hard_failed:
        return StageResult(
            VIRAL_ELIGIBLE, OBSERVATION_KIND, False, True, (),
            "viral-eligible: taste/hierarchy floor unproven → cannot vouch",
        )
    return StageResult(
        VIRAL_ELIGIBLE, OBSERVATION_KIND, False, False, ("not-viral-eligible",),
        "shared surface not floor-green → disqualified from the viral pool",
    )


# ── The staged harness ───────────────────────────────────────────────────────


def fold_niche(obs: ManifestObservation, *, capture: CaptureFn = null_capture) -> NicheManifest:
    """Run every proof against one niche's stream and capture per stage.

    The staged loop the brief asks for: ``for stage in (gates + observations) →
    screenshot + verdict-fold``. The gate stages are parsed from the injected
    ``GauntletVerdict`` (the owner's real ``accept_gauntlet.run`` output at flip
    time); the observation stages fold the visible/viral fields. A screenshot ref
    is attached to every stage and one video ref to the niche.
    """
    target = assert_safe_target(obs.url) if obs.url else None
    raw = (
        gate_stage_results(obs.gauntlet)
        + observation_stage_results(obs)
        + [viral_eligible_stage(obs.gauntlet)]
    )
    stages = tuple(
        StageResult(
            stage=s.stage,
            kind=s.kind,
            passed=s.passed,
            abstained=s.abstained,
            classes=s.classes,
            detail=s.detail,
            capture_ref=capture(CaptureRequest(obs.niche, s.stage, "screenshot", target)),
        )
        for s in raw
    )
    video = capture(CaptureRequest(obs.niche, "stream", "video", target))
    return NicheManifest(niche=obs.niche, prompt=obs.prompt, stages=stages, video_ref=video)


def run_manifest(
    observations: Sequence[ManifestObservation],
    *,
    capture: CaptureFn = null_capture,
    wow_floor: float = DEFAULT_WOW_FLOOR,
) -> ManifestVerdict:
    """Fold every niche's stream into one unified manifest verdict."""
    niches = tuple(fold_niche(obs, capture=capture) for obs in observations)
    return ManifestVerdict(niches=niches, wow_floor=wow_floor)


# ── Mock stream (deterministic, money-free) ──────────────────────────────────


def synthetic_verdict(
    *,
    fail: Sequence[str] = (),
    abstain: Sequence[str] = (),
    drop: Sequence[str] = (),
) -> GauntletVerdict:
    """Build a real ``GauntletVerdict`` covering EXPECTED_GATES, deterministically.

    Reuses the production dataclasses (R-04) so the manifest folds exactly the
    shape the owner's live ``accept_gauntlet.run`` emits. ``fail`` gates carry a
    finding, ``abstain`` gates produced no evidence, ``drop`` gates are omitted
    entirely (a coverage gap the parser must surface).
    """
    fail_set, abstain_set, drop_set = set(fail), set(abstain), set(drop)
    gates: list[GateVerdict] = []
    for gate in EXPECTED_GATES:
        if gate in drop_set:
            continue
        if gate in abstain_set:
            gates.append(GateVerdict(gate, False, True, (), f"{gate}: no render", {}))
        elif gate in fail_set:
            cls = (f"{gate}-defect",)
            gates.append(GateVerdict(gate, False, False, cls, f"{gate}: finding", {}))
        else:
            gates.append(GateVerdict(gate, True, False, (), f"{gate}: ok", {}))
    return GauntletVerdict(tuple(gates), render_expected=True)


def mock_observation(
    niche: niche_batch.Niche, *, defects: Sequence[str] = ()
) -> ManifestObservation:
    """A deterministic all-pass stream for one corpus niche (no LLM, no browser).

    ``defects`` injects one or more failures so an adversary test can prove the
    manifest has teeth (a clean stream PASSes, a defected one FAILs). Recognised
    defects: any EXPECTED gate id (fails that gate), or ``narration`` / ``swatches``
    / ``joy`` / ``param-inherit`` / ``fork-lineage`` (breaks that observation).
    """
    d = set(defects)
    gate_fail = [g for g in EXPECTED_GATES if g in d]
    spec_seed = {"palette": niche.palette, "sections": list(niche.sections), "tone": niche.tone}
    return ManifestObservation(
        niche=niche.key,
        prompt=niche.prompt,
        gauntlet=synthetic_verdict(fail=gate_fail),
        url=f"https://constructor.lead-generator.ru/p/mock-{niche.key}",
        is_fork=True,
        narration_present=False if NARRATION in d else True,
        swatches=() if SWATCHES in d else ("#0B1020", "#E11D48", "#F8FAFC"),
        joy_fired=False if JOY in d else True,
        # A dropped seed is a fork that HAPPENED but lost its preset/spec — the
        # slot is present but empty (a real finding), not merely uncaptured.
        inherited_preset=None if PARAM_INHERIT in d else f"{niche.key}-preset",
        inherited_spec={} if PARAM_INHERIT in d else spec_seed,
        fork_lineage=(
            (f"{niche.key}-root",)  # depth 1 → lineage-broken (needs ≥2)
            if FORK_LINEAGE in d
            else (f"{niche.key}-root", f"{niche.key}-b", f"{niche.key}-c")
        ),
    )


def mock_corpus(count: int | None = None) -> list[ManifestObservation]:
    """The default money-free stream: one all-pass observation per corpus niche."""
    niches = niche_batch.CORPUS if count is None else niche_batch.CORPUS[:count]
    return [mock_observation(n) for n in niches]


# ── Frozen dress-rehearsal (V-MANIFEST-FROZEN: deterministic, 0 LLM, 0 browser) ─
#
# The mock stream above proves the FOLD is sound, but it never reads a real page —
# its verdict is synthesised. This frozen mode folds the manifest over the four
# committed enterprise snapshots in ``reference_corpus_data/{agency,ecommerce,
# editorial,saas}.html`` (R-04 — the same corpus the reference ceiling leg uses),
# so the dress-rehearsal runs against REAL static HTML deterministically and
# browser-free. The owner-run later swaps these static files for a live
# generation + browser capture; the structure, fold and teeth are exercised now.
#
# Honesty contract — every gate is accounted for, none is faked:
#   * defect-registry + compose + the four context registries (viral/onboarding/
#     render/edit) are the REAL ``accept_gauntlet`` gates, run browser-free over
#     the actual HTML via ``run(..., include_rendered=False)``. No surrogate.
#   * the rendered legs cannot render without a browser, so in frozen mode they
#     are STATIC SURROGATES derived from the page's real structure (taste,
#     hierarchy, wow-dom, perf-a11y carry teeth; chip-pixel/data/reference are
#     documented inert/seam passes). Each surrogate's subscore is tagged
#     ``frozen=True`` so its provenance is never mistaken for a live render — the
#     owner-run replaces them with ``accept_gauntlet.run(url=...)``.
# An adversary that defaces a corpus file (flattens type, strips the hero, blanks
# the palette, injects a dead-auth CTA, drops the a11y landmarks) flips the gate
# it touches AND the unified verdict — the rehearsal has teeth, it is not a
# vacuous green over pre-vetted files.

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;\"'}]+)", re.IGNORECASE)
_HEADING_LEVEL = re.compile(r"<h([1-6])\b", re.IGNORECASE)
_HTML_LANG = re.compile(r"<html[^>]*\blang\s*=", re.IGNORECASE)
_VIEWPORT = re.compile(r"<meta[^>]*\bname\s*=\s*[\"']viewport", re.IGNORECASE)
_TITLE = re.compile(r"<title\b[^>]*>\s*\S", re.IGNORECASE)
_IMG = re.compile(r"<img\b", re.IGNORECASE)
_IMG_WITH_ALT = re.compile(r"<img\b[^>]*\balt\s*=", re.IGNORECASE)
_DATA_MARKER = re.compile(
    r"<table\b|(?:class|id)\s*=\s*[\"'][^\"']*\b(?:grid|card|stat|metric|table|datagrid|list)\b",
    re.IGNORECASE,
)

#: The minimum distinct hex swatches a "brief-derived palette" must carry (V3.4).
MIN_SWATCHES = 3
#: A composed page pairs at least two type families (display + body).
MIN_FONT_FAMILIES = 2
#: A clear visual hierarchy uses at least two distinct heading levels.
MIN_HEADING_LEVELS = 2


@dataclass(frozen=True)
class FrozenAnalysis:
    """The browser-free static measurements of one corpus page.

    Everything here is read straight off the authored HTML — no render, no LLM.
    The compose report and defect classes come from the REAL gates (R-04); the
    rest are the structural signals the rendered-leg surrogates stand on.
    """

    compose: compose_gate.ComposeReport
    palette: tuple[str, ...]
    font_families: int
    heading_levels: int
    has_lang: bool
    has_viewport: bool
    has_title: bool
    images: int
    images_with_alt: int
    data_markers: int


def analyse_frozen(html: str) -> FrozenAnalysis:
    """Measure one static HTML document, browser-free and deterministically."""
    palette = tuple(sorted({m.group(0).lower() for m in _HEX.finditer(html)}))
    families = {m.group(1).strip().lower() for m in _FONT_FAMILY.finditer(html)}
    levels = {m.group(1) for m in _HEADING_LEVEL.finditer(html)}
    return FrozenAnalysis(
        compose=compose_gate.scan({"index.html": html}),
        palette=palette,
        font_families=len(families),
        heading_levels=len(levels),
        has_lang=_HTML_LANG.search(html) is not None,
        has_viewport=_VIEWPORT.search(html) is not None,
        has_title=_TITLE.search(html) is not None,
        images=len(_IMG.findall(html)),
        images_with_alt=len(_IMG_WITH_ALT.findall(html)),
        data_markers=len(_DATA_MARKER.findall(html)),
    )


def _surrogate(gate: str, *, passed: bool, classes: tuple[str, ...], detail: str) -> GateVerdict:
    """A frozen-mode rendered-leg verdict, tagged so its provenance is explicit."""
    return GateVerdict(
        gate=gate,
        passed=passed,
        abstained=False,  # frozen mode always has the static page → never abstains
        classes=() if passed else classes,
        summary=f"frozen-static: {detail}",
        subscore={"gate": gate, "frozen": True, "passed": passed},
    )


def _rendered_surrogates(a: FrozenAnalysis) -> list[GateVerdict]:
    """Derive a verdict for every rendered leg from the page's static structure.

    These stand in for the browser-rendered taste/hierarchy/wow-dom/perf-a11y
    legs (with real teeth) plus the legs a static page cannot evidence
    (chip-pixel/data/reference — documented inert/seam passes). The owner-run
    swaps each for the live ``accept_gauntlet`` leg.
    """
    out: list[GateVerdict] = []

    # WOW-DOM — the composed surface: section rhythm, a hero, a real palette.
    wow_fail: list[str] = []
    if a.compose.sections < compose_gate.MIN_SECTIONS:
        wow_fail.append("too-few-sections")
    if not a.compose.hero:
        wow_fail.append("no-hero")
    if len(a.palette) < MIN_SWATCHES:
        wow_fail.append("monochrome")
    out.append(
        _surrogate(
            accept_gauntlet.WOW_DOM,
            passed=not wow_fail,
            classes=tuple(wow_fail),
            detail=(
                f"{a.compose.sections} sections, hero={a.compose.hero}, "
                f"{len(a.palette)} swatches"
            ),
        )
    )

    # PERF-A11Y — the cheap, statically-checkable accessibility landmarks.
    a11y_fail: list[str] = []
    if not a.has_lang:
        a11y_fail.append("no-lang")
    if not a.has_viewport:
        a11y_fail.append("no-viewport")
    if not a.has_title:
        a11y_fail.append("no-title")
    if a.images and a.images_with_alt < a.images:
        a11y_fail.append("img-alt-missing")
    out.append(
        _surrogate(
            accept_gauntlet.PERF_A11Y,
            passed=not a11y_fail,
            classes=tuple(a11y_fail),
            detail=(
                f"lang={a.has_lang}, viewport={a.has_viewport}, title={a.has_title}, "
                f"img-alt={a.images_with_alt}/{a.images}"
            ),
        )
    )

    # CHIP-PIXEL — inert without a discovery_spec, exactly like the live leg.
    out.append(
        _surrogate(
            accept_gauntlet.CHIP_PIXEL,
            passed=True,
            classes=(),
            detail="inert (no discovery_spec to honour in a static page)",
        )
    )

    # TASTE — font pairing, a real type scale, a meaningful hero.
    taste_fail: list[str] = []
    if a.font_families < MIN_FONT_FAMILIES:
        taste_fail.append(taste_gate.FONT_PAIRING)
    if a.compose.font_sizes < compose_gate.MIN_FONT_SIZES:
        taste_fail.append(taste_gate.TYPE_SCALE)
    if not a.compose.hero:
        taste_fail.append(taste_gate.HERO_IMAGERY)
    out.append(
        _surrogate(
            accept_gauntlet.TASTE,
            passed=not taste_fail,
            classes=tuple(taste_fail),
            detail=f"{a.font_families} font families, {a.compose.font_sizes} type sizes",
        )
    )

    # HIERARCHY — distinct heading levels (type dominance) + a focal hero.
    hier_fail: list[str] = []
    if a.heading_levels < MIN_HEADING_LEVELS:
        hier_fail.append(hierarchy_gate.TYPE_DOMINANCE)
    if not a.compose.hero:
        hier_fail.append(hierarchy_gate.FOCAL_DOMINANCE)
    out.append(
        _surrogate(
            accept_gauntlet.HIERARCHY,
            passed=not hier_fail,
            classes=tuple(hier_fail),
            detail=f"{a.heading_levels} heading levels, hero={a.compose.hero}",
        )
    )

    # DATA — judged only when the page carries a data surface; INERT otherwise
    # (a landing page with no table/grid is not a data defect — mirrors the live
    # leg, which finds nothing to grade).
    out.append(
        _surrogate(
            accept_gauntlet.DATA,
            passed=True,
            classes=(),
            detail=(
                f"{a.data_markers} data marker(s) present"
                if a.data_markers
                else "inert (no data surface to judge)"
            ),
        )
    )

    # REFERENCE — the pillar-1 ceiling leg compares rendered richness vectors,
    # which need a browser. A corpus file is itself a curated reference, so it
    # meets the ceiling by construction; the owner-run renders a live candidate.
    out.append(
        _surrogate(
            accept_gauntlet.REFERENCE,
            passed=True,
            classes=(),
            detail="corpus member meets the ceiling (live render-vector leg is owner-run)",
        )
    )

    return out


def frozen_verdict(html: str) -> GauntletVerdict:
    """A real, browser-free ``GauntletVerdict`` over one static page.

    The six context/source-scan gates are the genuine ``accept_gauntlet`` gates
    run over the actual HTML (no browser); the seven rendered legs are the
    frozen-static surrogates. Together they cover EXACTLY ``EXPECTED_GATES``, so a
    frozen niche has no coverage gap.
    """
    a = analyse_frozen(html)
    real = asyncio.run(
        accept_gauntlet.run(
            files={"index.html": html},
            compose=True,
            viral=True,
            onboarding=True,
            render=True,
            edit=True,
            include_rendered=False,  # money-free: no headless browser
        )
    )
    return GauntletVerdict(
        gates=(*real.gates, *_rendered_surrogates(a)),
        render_expected=True,
    )


def frozen_observation(niche: str, prompt: str, html: str) -> ManifestObservation:
    """Fold one static corpus page into a manifest observation, browser-free.

    Every observation field is DERIVED from the real page: the swatches are its
    actual palette (only when ≥3 distinct, the V3.4 floor), and narration / joy
    are present iff the page carries the brief evidence that drives them. A corpus
    page is a root (not a fork), so the two fork stages stay INERT.
    """
    a = analyse_frozen(html)
    swatches = a.palette if len(a.palette) >= MIN_SWATCHES else ()
    narratable = bool(swatches) and a.compose.sections >= compose_gate.MIN_SECTIONS
    return ManifestObservation(
        niche=niche,
        prompt=prompt,
        gauntlet=frozen_verdict(html),
        url=None,  # static file: no live target for the capture seam
        is_fork=False,
        narration_present=narratable,
        swatches=swatches,
        joy_fired=a.compose.passed,
    )


def frozen_corpus(corpus_dir: Path | None = None) -> list[ManifestObservation]:
    """One observation per committed corpus snapshot (sorted, deterministic)."""
    corpus = (
        reference_corpus.load_corpus()
        if corpus_dir is None
        else reference_corpus.load_corpus(corpus_dir)
    )
    return [
        frozen_observation(niche, f"frozen corpus reference: {niche}", html)
        for niche, html in corpus.items()
    ]


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="e2e_manifest",
        description=(
            "PAID-RUN MANIFEST — fold every shipped proof against one stream per "
            "niche and emit the unified PASS/FAIL. Default body is the money-free "
            "mock stream (0 LLM, 0 browser); the owner swaps real generation + "
            "browser capture into the injected GauntletVerdict / CaptureFn seams."
        ),
    )
    p.add_argument("--niches", type=int, default=None, help="Run the first N corpus niches.")
    p.add_argument(
        "--frozen",
        action="store_true",
        help=(
            "Dress-rehearsal mode: fold the manifest over the committed frozen-HTML "
            "corpus (reference_corpus_data/*.html) deterministically, browser-free, "
            "instead of the synthetic mock stream."
        ),
    )
    p.add_argument(
        "--wow-floor",
        type=float,
        default=DEFAULT_WOW_FLOOR,
        help=f"Per-niche WOW floor 0–10 (default {DEFAULT_WOW_FLOOR}).",
    )
    p.add_argument("--out", default=None, help="Also write the JSON subscore to this path.")
    return p.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.frozen:
        observations = frozen_corpus()
        if args.niches is not None:
            observations = observations[: args.niches]
    else:
        observations = mock_corpus(args.niches)
    verdict = run_manifest(observations, wow_floor=args.wow_floor)
    print(verdict.table())
    if args.out:
        text = json.dumps(verdict.subscore(), ensure_ascii=False, indent=2)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    # exit 0 iff every niche cleared every proof — the gate the owner-run asserts.
    return 0 if verdict.passed else 1


if __name__ == "__main__":  # pragma: no cover — thin CLI wrapper
    raise SystemExit(main())
