#!/usr/bin/env python3
"""V1.6 16/5e — FLIP-MILESTONE: the falsifiable condition to flip the entity gate ON.

``acceptance_entity_composition_gate`` ships default-OFF (``config.py``). While
OFF, the entity hot-path — the surface the flagship actually generates — runs the
awwwards composition floor (taste + hierarchy) but does NOT block on it. The flag
was OFF "until the legs are calibrated for real entity apps" (16/5b), with no
written, machine-checkable condition for when it is safe to flip. An undated,
eyeballed flip is exactly the recurrence the ratchet exists to kill: the floor
that "protects 80% of the corpus" must turn ON against a *number*, not a vibe.

This module is that number. The flip is permitted **iff all three hold**:

  1. **Corpus PASSES.** The N≥5 canonical good entity niches (sushi / fitness /
     shop / school / fintech), each scored over its WOW/content route (16/5d) at
     desktop AND mobile, ALL pass the composition gauntlet — ``tally.all_passed``.
     One false-fail on a good app means the legs still flood false-positives; the
     flag must stay OFF.
  2. **BASELINE FAILS (teeth).** A bootstrap-baseline-equivalent app, scored over
     the same route, MUST hard-fail. A floor that passes everything — including a
     known-bad page — has no teeth and is worse than no floor (false green).
  3. **FREEFORM intact.** The freeform composition matrix (14/5) still passes —
     flipping the entity path must not regress the branch already gated.

Two clean halves, like ``niche_batch``:

  * The DECISION is pure (:func:`evaluate_flip`, :func:`check_consistency`,
    :class:`MilestoneVerdict`) — replayable, money-free, fully unit-tested.
  * The EVIDENCE (:func:`run_milestone`) reuses ``niche_batch`` to score apps
    that are *already running* (the owner generates the corpus once — the paid
    16/5e step — and hands their URLs here; this harness never generates).

``check_consistency`` is the standing CI invariant: a flag that is ON must be
backed by a passing milestone. With the flag at its committed default (OFF) the
guard is always green; the instant someone flips it ON in config without a
recorded passing milestone, the guard — and the unit test that calls it — turns
RED. That is what makes "awwwards with one generation" *gated on the path the
flagship uses*, not just promised in prose.

Canon: R-01 (one ``run_milestone`` call hides corpus-score + baseline-score +
fold), R-04 (reuses ``niche_batch`` scoring/route/tally — zero duplicated gate,
URL, or aggregation logic), R-10 (a provision/render miss degrades to a recorded
fail, never crashes the check).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# niche_batch is a sibling script; the src-layout package holds the live config.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import niche_batch as nb  # noqa: E402

#: The flag this milestone gates the flip of — the single source of truth.
FLAG_NAME = "acceptance_entity_composition_gate"


# ── Pure decision ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MilestoneVerdict:
    """The three-clause flip decision, plus the human-readable reasons.

    ``flip_permitted`` is the conjunction of all three clauses; ``reasons`` lists
    the clauses that are NOT satisfied (empty ⇒ permitted) so a red result always
    says *why* the floor cannot be turned on yet.
    """

    corpus_all_passed: bool
    baseline_failed: bool
    freeform_ok: bool
    reasons: tuple[str, ...]

    @property
    def flip_permitted(self) -> bool:
        """All three clauses hold — the flag may be flipped ON."""
        return self.corpus_all_passed and self.baseline_failed and self.freeform_ok

    def report(self) -> dict[str, Any]:
        return {
            "corpus_all_passed": self.corpus_all_passed,
            "baseline_failed": self.baseline_failed,
            "freeform_ok": self.freeform_ok,
            "flip_permitted": self.flip_permitted,
            "reasons": list(self.reasons),
        }


def evaluate_flip(
    corpus_tally: dict[str, Any],
    *,
    baseline_passed: bool,
    freeform_ok: bool = True,
) -> MilestoneVerdict:
    """Fold the corpus tally + baseline verdict into the flip decision (pure).

    ``corpus_tally`` is a :func:`niche_batch.tally` report — its ``all_passed``
    is the corpus clause. ``baseline_passed`` is the bootstrap-baseline run's
    strict verdict: the teeth clause requires it to be ``False``. ``freeform_ok``
    is the 14/5 regression signal (the freeform composition matrix still green).
    """
    corpus_all_passed = bool(corpus_tally.get("all_passed"))
    baseline_failed = not baseline_passed

    reasons: list[str] = []
    if not corpus_all_passed:
        failing = [
            k
            for k, v in (corpus_tally.get("per_niche") or {}).items()
            if v.get("pass_rate", 0) < 1.0
        ]
        detail = f" (failing: {', '.join(sorted(failing))})" if failing else ""
        reasons.append(f"corpus did NOT all-pass{detail}")
    new_classes = corpus_tally.get("new_defect_classes") or []
    if new_classes:
        reasons.append(f"new defect class(es) appeared: {', '.join(new_classes)}")
    if not baseline_failed:
        reasons.append("bootstrap baseline PASSED — composition floor has no teeth")
    if not freeform_ok:
        reasons.append("freeform composition (14/5 matrix) regressed")

    return MilestoneVerdict(
        corpus_all_passed=corpus_all_passed and not new_classes,
        baseline_failed=baseline_failed,
        freeform_ok=freeform_ok,
        reasons=tuple(reasons),
    )


def check_consistency(flag_on: bool, verdict: MilestoneVerdict) -> tuple[bool, str]:
    """The standing invariant: a flag that is ON must be backed by a passing milestone.

    OFF is always consistent — not gating is never *unsafe*, only un-ambitious.
    ON without ``flip_permitted`` is the dangerous state (the floor blocks ship on
    a leg that false-fails good apps, or has no teeth) → the exit-1 contract.
    """
    if not flag_on:
        return True, f"{FLAG_NAME} is OFF — consistent (floor not yet enforced)"
    if verdict.flip_permitted:
        return True, f"{FLAG_NAME} is ON and the milestone passes — consistent"
    why = "; ".join(verdict.reasons) or "milestone not met"
    return False, f"{FLAG_NAME} is ON but the milestone does NOT pass — {why}"


# ── Evidence (reuses niche_batch — money-free over already-running apps) ──────


async def run_milestone(
    urls: dict[str, str],
    baseline_url: str | None,
    *,
    niches: Sequence[nb.Niche] | None = None,
    runs: int = 1,
    freeform_ok: bool = True,
    known_classes: Sequence[str] = (),
    **score_kw: Any,
) -> tuple[MilestoneVerdict, dict[str, Any]]:
    """Score the good corpus + the baseline, then decide the flip (R-01).

    Returns ``(verdict, evidence)`` where ``evidence`` carries the full corpus
    tally and the baseline run for the JSON report. No generation happens here —
    ``urls`` / ``baseline_url`` point at containers the owner already provisioned.
    """
    corpus = tuple(niches) if niches is not None else nb.CORPUS
    provision = nb.urls_provisioner(urls)
    results = await nb.run_batch(corpus, runs, provision, **score_kw)
    corpus_tally = nb.tally(
        results,
        niches=len(corpus),
        runs_per_niche=runs,
        known_classes=known_classes,
    )

    if baseline_url is None:
        baseline_passed = True  # no baseline evidence ⇒ teeth UNPROVEN ⇒ block flip
        baseline_run: dict[str, Any] | None = None
    else:
        # Score the baseline like any niche — same route-resolve, same legs.
        baseline_result = await nb.score_app(baseline_url, corpus[0], **score_kw)
        baseline_passed = baseline_result.passed
        baseline_run = {
            "url": baseline_url,
            "route": baseline_result.route,
            "passed": baseline_passed,
            "failed_classes": list(baseline_result.failed_classes),
        }

    verdict = evaluate_flip(
        corpus_tally, baseline_passed=baseline_passed, freeform_ok=freeform_ok
    )
    evidence = {
        "corpus_tally": corpus_tally,
        "baseline": baseline_run,
        "baseline_provided": baseline_url is not None,
    }
    return verdict, evidence


# ── CLI ──────────────────────────────────────────────────────────────────────


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _current_flag() -> bool:
    """The committed default of the flag (imported live, never hard-coded)."""
    from omnia_api.core.config import get_settings

    return bool(getattr(get_settings(), FLAG_NAME))


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="flip_milestone",
        description="Decide whether the entity composition gate may be flipped ON.",
    )
    p.add_argument(
        "--urls",
        required=True,
        help="JSON file mapping niche key -> base URL of an ALREADY running good app.",
    )
    p.add_argument(
        "--baseline-url",
        default=None,
        help="Base URL of a bootstrap-baseline-equivalent app (the teeth check; "
        "it MUST fail). Omitted ⇒ teeth unproven ⇒ flip blocked.",
    )
    p.add_argument("--niches", type=int, default=None, help="Use the first N corpus niches.")
    p.add_argument("--only", default=None, help="Comma-separated niche keys to score.")
    p.add_argument("--runs", type=int, default=1, help="Runs (re-scores) per niche.")
    p.add_argument(
        "--known-classes",
        default=None,
        help="JSON array of baseline defect classes; classes outside it are NEW.",
    )
    p.add_argument(
        "--freeform-broken",
        action="store_true",
        help="Mark the freeform 14/5 matrix as regressed (adversarial / CI signal).",
    )
    p.add_argument(
        "--mode",
        choices=("guard", "gate"),
        default="guard",
        help="guard: exit 0 iff the CURRENT flag state is consistent (CI invariant). "
        "gate: exit 0 iff the milestone permits the flip (owner authorizes a flip).",
    )
    p.add_argument(
        "--flag-on",
        dest="flag_on",
        action="store_true",
        default=None,
        help="Override the flag state for the guard (default: read live config).",
    )
    p.add_argument(
        "--flag-off", dest="flag_on", action="store_false", help="Override flag → OFF."
    )
    p.add_argument("--out", default=None, help="Also write the JSON report to this path.")
    return p.parse_args(list(argv))


async def _run_cli(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    names = [s.strip() for s in args.only.split(",")] if args.only else None
    corpus = nb.select_niches(names, args.niches)
    urls = _load_json(args.urls)
    known = _load_json(args.known_classes) if args.known_classes else ()
    verdict, evidence = await run_milestone(
        urls,
        args.baseline_url,
        niches=corpus,
        runs=args.runs,
        freeform_ok=not args.freeform_broken,
        known_classes=known,
    )
    flag_on = _current_flag() if args.flag_on is None else args.flag_on
    consistent, message = check_consistency(flag_on, verdict)
    report = {
        "flag": FLAG_NAME,
        "flag_on": flag_on,
        "mode": args.mode,
        "milestone": verdict.report(),
        "consistent": consistent,
        "message": message,
        "evidence": evidence,
    }
    if args.mode == "gate":
        exit_code = 0 if verdict.flip_permitted else 1
    else:  # guard
        exit_code = 0 if consistent else 1
    return report, exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report, exit_code = asyncio.run(_run_cli(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":  # pragma: no cover — thin CLI wrapper
    raise SystemExit(main())
