#!/usr/bin/env python3
"""V1.13c — ADVERSARY-PRE-PROOF: does the pillar-1 CEILING actually have teeth?

Before the owner flips ``acceptance_gauntlet_reference_gate=True`` (config.py:353,
default OFF — so the REFERENCE leg never bites in prod today), this proves the
gate would bite mediocrity rather than certify it. It renders a known-mediocre
adversary (``tests/fixtures/bootstrap-baseline.html`` by default — the same shape
the taste gate keeps red) against EVERY frozen corpus niche and checks the
adversary regresses on at least ``MIN_REGRESSIONS`` of the five richness axes on
each one.

Money-free, 0 LLM — pure Chromium render + the deterministic comparator in
:mod:`omnia_api.services.reference_corpus`.

Exit codes:
    0  TEETH   — adversary falls below the corpus on EVERY niche → safe to flip.
    1  LEAKY   — adversary held the floor on some niche → the ceiling would let
                 mediocrity through; DO NOT flip until the corpus/gates are fixed.
    2  ABSTAIN — no Chromium on this host, or no corpus → no verdict (run inside
                 the prod-worker container for real teeth).

    python scripts/prove_reference_ceiling.py
    python scripts/prove_reference_ceiling.py --adversary path/to/other.html
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make the src-layout package importable when run as `python scripts/...`.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnia_api.services import reference_corpus as rc  # noqa: E402

_DEFAULT_ADVERSARY = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bootstrap-baseline.html"
)


async def _run(adversary_html: str) -> int:
    corpus = rc.load_corpus()
    if not corpus:
        print(f"no reference corpus found in {rc.CORPUS_DIR}", file=sys.stderr)
        return 2

    adv_vec, adv_rendered = await rc.vector_of_html(adversary_html)
    if not adv_rendered:
        print(
            "ABSTAIN: no Chromium available (adversary did not render) — run "
            "inside the prod-worker container for real teeth.",
            file=sys.stderr,
        )
        return 2

    leaky: list[str] = []
    abstained = False
    print(
        f"adversary holds {sum(adv_vec.values())}/{len(rc.RICHNESS_AXES)} axes: "
        f"{[a for a in rc.RICHNESS_AXES if adv_vec[a]] or '∅'}"
    )
    print(f"floor: adversary must regress on >= {rc.MIN_REGRESSIONS} axes per niche\n")

    for niche, ref_html in sorted(corpus.items()):
        ref_vec, ref_rendered = await rc.vector_of_html(ref_html)
        if not ref_rendered:
            print(f"  {niche}: ABSTAIN (reference did not render)")
            abstained = True
            continue
        regressed = rc.adversary_regressions(adv_vec, ref_vec)
        has_teeth = len(regressed) >= rc.MIN_REGRESSIONS
        verdict = "BELOW ✓" if has_teeth else "HELD-FLOOR ✗"
        print(
            f"  {niche}: {verdict} — adversary regresses on "
            f"{len(regressed)}/{len(rc.RICHNESS_AXES)} axes: "
            f"{list(regressed) or '∅'}"
        )
        if not has_teeth:
            leaky.append(niche)

    if leaky:
        print(
            f"\nLEAKY: adversary held the floor on {', '.join(leaky)} — the "
            "ceiling would certify mediocrity. DO NOT flip reference_gate.",
            file=sys.stderr,
        )
        return 1
    if abstained:
        print(
            "\nABSTAIN: a reference niche did not render — no full verdict.",
            file=sys.stderr,
        )
        return 2
    print(
        "\nTEETH: adversary falls below the corpus on every niche — "
        "reference_gate is safe to flip."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adversary",
        dest="adversary",
        default=str(_DEFAULT_ADVERSARY),
        help="path to the known-mediocre adversary HTML (default: bootstrap-baseline)",
    )
    args = parser.parse_args(argv)
    path = Path(args.adversary)
    if not path.is_file():
        parser.error(f"adversary not found: {path}")
    return asyncio.run(_run(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    raise SystemExit(main())
