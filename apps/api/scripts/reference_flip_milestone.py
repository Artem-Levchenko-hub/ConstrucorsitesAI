#!/usr/bin/env python3
"""V1.13c — FLIP-RUNBOOK: the falsifiable condition to flip the REFERENCE gate ON.

``acceptance_gauntlet_reference_gate`` ships default-OFF (``config.py``). While
OFF the pillar-1 CEILING leg renders and scores but never *blocks* — so the
"awwwards-with-one-generation" ceiling is asserted on ZERO live generations. The
flag had only a prose flip-condition ("a fresh generation must clear the bar
without a model change"), with no machine-checkable, dated milestone. An undated,
eyeballed flip is exactly the recurrence the ratchet exists to kill: a ceiling
that "protects pillar 1" must turn ON against a *number*, not a vibe.

This module is that number — the runbook made executable. The flip is permitted
**iff all three hold**:

  1. **CANDIDATES CLEAR (ceiling proven on fresh gens).** At least ``min_candidates``
     fresh generations, each rendered and graded against the frozen corpus, MEET
     OR BEAT every curated reference (``ReferenceReport.passed``). If even one
     fresh generation falls below the corpus, the generator does not yet clear the
     bar — flipping the gate ON would just reject the flagship's own output.
  2. **ADVERSARY BELOW (teeth).** A known-mediocre baseline regresses on at least
     ``min_regressions`` of the five richness axes against EVERY corpus niche
     (the ``prove_reference_ceiling`` proof, reused here). A ceiling that a bad
     page can clear has no teeth and would certify mediocrity.
  3. **CORPUS PRESENT.** The frozen reference corpus is non-empty — there is a
     bar to clear at all.

Two clean halves, exactly like ``flip_milestone`` (16/5e):

  * The DECISION is pure (:func:`evaluate_reference_flip`, :func:`check_consistency`,
    :class:`ReferenceMilestoneVerdict`) — replayable, money-free, fully unit-tested.
  * The EVIDENCE (:func:`run_reference_milestone`) reuses
    :mod:`omnia_api.services.reference_corpus` to render candidate generations the
    owner already produced (the paid corpus-run) + the adversary, then folds them
    into the decision. This harness never GENERATES — it grades what it is handed.

``check_consistency`` is the standing CI invariant: a flag that is ON must be
backed by a passing milestone. With the flag at its committed default (OFF) the
guard is always green; the instant someone flips ``reference_gate`` ON in config
without a recorded passing milestone, the guard — and the unit test that calls it
— turns RED. That is what makes the pillar-1 ceiling *enforced against a path the
flagship uses*, not just promised in prose.

Owner runbook (the PAID corpus-run, once):

  1. Generate N fresh apps across distinct niches (no model change, no manual
     edits) and capture each as a static ``index.html`` (or point at a live URL).
  2. ``python scripts/reference_flip_milestone.py --candidates <dir-or-files> \\
        --mode gate``  → exit 0 means clauses 1–3 hold; the flip is authorised.
  3. Flip ``acceptance_gauntlet_reference_gate=True`` in ``config.py`` and commit
     the milestone report (``--out``) alongside, so the guard stays green.
  4. CI runs ``--mode guard`` every build: ON-without-milestone fails fast.

Canon: R-01 (one ``run_reference_milestone`` call hides render + grade + fold),
R-04 (reuses ``reference_corpus`` — zero duplicated gate / axis / compare logic),
R-10 (a render miss degrades to a recorded abstain, never crashes the check).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the src-layout package importable when run as `python scripts/...`.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnia_api.services import reference_corpus as rc  # noqa: E402

#: The flag this milestone gates the flip of — the single source of truth.
FLAG_NAME = "acceptance_gauntlet_reference_gate"

_DEFAULT_ADVERSARY = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bootstrap-baseline.html"
)


# ── Pure decision ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReferenceMilestoneVerdict:
    """The three-clause flip decision, plus the human-readable reasons.

    ``flip_permitted`` is the conjunction of all three clauses; ``reasons`` lists
    the clauses that are NOT satisfied (empty ⇒ permitted) so a red result always
    says *why* the ceiling cannot be turned on yet.
    """

    candidates_cleared: bool
    adversary_below: bool
    corpus_present: bool
    reasons: tuple[str, ...]

    @property
    def flip_permitted(self) -> bool:
        """All three clauses hold — the flag may be flipped ON."""
        return self.candidates_cleared and self.adversary_below and self.corpus_present

    def report(self) -> dict[str, Any]:
        return {
            "candidates_cleared": self.candidates_cleared,
            "adversary_below": self.adversary_below,
            "corpus_present": self.corpus_present,
            "flip_permitted": self.flip_permitted,
            "reasons": list(self.reasons),
        }


def evaluate_reference_flip(
    candidate_passes: Sequence[bool],
    adversary_regressions_per_niche: Mapping[str, int],
    *,
    corpus_niches: Sequence[str],
    min_candidates: int = 1,
    min_regressions: int = rc.MIN_REGRESSIONS,
) -> ReferenceMilestoneVerdict:
    """Fold the candidate verdicts + adversary regressions into the flip decision (pure).

    ``candidate_passes`` is one strict ``ReferenceReport.passed`` per fresh
    generation (meets-or-beats EVERY corpus niche). ``adversary_regressions_per_niche``
    maps each corpus niche to how many of the five axes a known-mediocre baseline
    regressed on against it. ``corpus_niches`` is the frozen corpus's niche keys —
    the teeth clause requires the adversary to fall below EVERY one of them, so a
    niche missing from the regression map is a coverage gap (not silently a pass).
    """
    niches = tuple(corpus_niches)
    corpus_present = len(niches) > 0

    enough_candidates = len(candidate_passes) >= max(min_candidates, 1)
    candidates_cleared = enough_candidates and all(candidate_passes)

    adversary_below = corpus_present and all(
        adversary_regressions_per_niche.get(n, 0) >= min_regressions for n in niches
    )

    reasons: list[str] = []
    if not corpus_present:
        reasons.append("reference corpus is empty — no ceiling to clear")
    if not enough_candidates:
        reasons.append(
            f"only {len(candidate_passes)} fresh candidate(s) scored — "
            f"need >= {max(min_candidates, 1)}"
        )
    elif not candidates_cleared:
        below = sum(1 for p in candidate_passes if not p)
        reasons.append(
            f"{below}/{len(candidate_passes)} fresh candidate(s) fell BELOW the "
            "corpus — the generator does not yet clear the bar"
        )
    if corpus_present and not adversary_below:
        leaky = [
            n
            for n in niches
            if adversary_regressions_per_niche.get(n, 0) < min_regressions
        ]
        reasons.append(
            f"adversary held the floor on {', '.join(leaky)} — the ceiling has no "
            f"teeth (needs >= {min_regressions} regressions per niche)"
        )

    return ReferenceMilestoneVerdict(
        candidates_cleared=candidates_cleared,
        adversary_below=adversary_below,
        corpus_present=corpus_present,
        reasons=tuple(reasons),
    )


def check_consistency(
    flag_on: bool, verdict: ReferenceMilestoneVerdict
) -> tuple[bool, str]:
    """The standing invariant: a flag that is ON must be backed by a passing milestone.

    OFF is always consistent — not enforcing the ceiling is never *unsafe*, only
    un-ambitious. ON without ``flip_permitted`` is the dangerous state (the gate
    hard-fails ship on a corpus the generator can't clear, or has no teeth) → the
    exit-1 contract.
    """
    if not flag_on:
        return True, f"{FLAG_NAME} is OFF — consistent (ceiling not yet enforced)"
    if verdict.flip_permitted:
        return True, f"{FLAG_NAME} is ON and the milestone passes — consistent"
    why = "; ".join(verdict.reasons) or "milestone not met"
    return False, f"{FLAG_NAME} is ON but the milestone does NOT pass — {why}"


# ── Evidence (reuses reference_corpus — money-free over already-built artefacts) ──


async def run_reference_milestone(
    candidate_htmls: Sequence[str],
    adversary_html: str | None,
    *,
    corpus_dir: Path = rc.CORPUS_DIR,
    min_candidates: int = 1,
    min_regressions: int = rc.MIN_REGRESSIONS,
) -> tuple[ReferenceMilestoneVerdict, dict[str, Any]]:
    """Render the fresh candidates + the adversary, grade them, decide the flip (R-01).

    Returns ``(verdict, evidence)``. No generation happens here — the candidates
    are static ``index.html`` snapshots of apps the owner already produced.
    """
    corpus = rc.load_corpus(corpus_dir)
    corpus_niches = tuple(sorted(corpus))

    candidate_passes: list[bool] = []
    candidate_rows: list[dict[str, Any]] = []
    for i, html in enumerate(candidate_htmls):
        comps = await rc.compare_to_corpus(html, corpus_dir=corpus_dir)
        rep = rc.ReferenceReport(tuple(comps))
        candidate_passes.append(rep.passed)
        candidate_rows.append(
            {
                "index": i,
                "passed": rep.passed,
                "niches_met": rep.niches_met,
                "rendered": rep.rendered,
                "summary": rep.summary(),
            }
        )

    regressions: dict[str, int] = {}
    adversary_row: dict[str, Any] | None = None
    if adversary_html is not None and corpus_niches:
        adv_vec, adv_rendered = await rc.vector_of_html(adversary_html)
        if adv_rendered:
            per_niche: dict[str, int] = {}
            for niche, ref_html in corpus.items():
                ref_vec, ref_rendered = await rc.vector_of_html(ref_html)
                if not ref_rendered:
                    continue  # render miss → coverage gap (not a pass)
                per_niche[niche] = len(rc.adversary_regressions(adv_vec, ref_vec))
            regressions = per_niche
            adversary_row = {"rendered": True, "regressions_per_niche": per_niche}
        else:
            adversary_row = {"rendered": False, "regressions_per_niche": {}}

    verdict = evaluate_reference_flip(
        candidate_passes,
        regressions,
        corpus_niches=corpus_niches,
        min_candidates=min_candidates,
        min_regressions=min_regressions,
    )
    evidence = {
        "corpus_niches": list(corpus_niches),
        "candidates": candidate_rows,
        "adversary": adversary_row,
        "adversary_provided": adversary_html is not None,
    }
    return verdict, evidence


# ── CLI ──────────────────────────────────────────────────────────────────────


def _collect_candidate_htmls(paths: Sequence[str]) -> list[str]:
    """Read candidate ``index.html`` snapshots from files and/or directories.

    A directory contributes every ``*.html`` it holds (sorted); a file is read
    directly. Missing paths are a hard CLI error (the owner pointed at nothing).
    """
    out: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for html in sorted(p.glob("*.html")):
                out.append(html.read_text(encoding="utf-8"))
        elif p.is_file():
            out.append(p.read_text(encoding="utf-8"))
        else:
            raise FileNotFoundError(f"candidate path not found: {p}")
    return out


def _read_adversary(path: str) -> str | None:
    """Read the adversary HTML, or ``None`` when it is not present here.

    The default adversary is a ``tests/`` fixture, which the prod image
    ``.dockerignore``s — so a missing file degrades to ``None`` (the teeth clause
    then abstains) rather than crashing the runbook (R-10).
    """
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _current_flag() -> bool:
    """The committed default of the flag (imported live, never hard-coded)."""
    from omnia_api.core.config import get_settings

    return bool(getattr(get_settings(), FLAG_NAME))


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reference_flip_milestone",
        description="Decide whether the pillar-1 reference (CEILING) gate may flip ON.",
    )
    p.add_argument(
        "--candidates",
        nargs="*",
        default=[],
        help="Files and/or dirs of fresh-generation index.html snapshots to grade.",
    )
    p.add_argument(
        "--adversary",
        default=str(_DEFAULT_ADVERSARY),
        help="Known-mediocre baseline HTML (teeth check; default: bootstrap-baseline).",
    )
    p.add_argument(
        "--min-candidates",
        type=int,
        default=1,
        help="Fresh candidates that must ALL clear the corpus (default 1).",
    )
    p.add_argument(
        "--mode",
        choices=("guard", "gate"),
        default="guard",
        help="guard: exit 0 iff the CURRENT flag state is consistent (CI invariant). "
        "gate: exit 0 iff the milestone permits the flip (owner authorises a flip).",
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
    candidate_htmls = _collect_candidate_htmls(args.candidates)
    adversary_html = _read_adversary(args.adversary)

    verdict, evidence = await run_reference_milestone(
        candidate_htmls,
        adversary_html,
        min_candidates=args.min_candidates,
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
