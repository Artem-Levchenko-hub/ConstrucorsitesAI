#!/usr/bin/env python3
"""V1.13a — CLI: does a candidate page MEET OR BEAT the reference corpus?

A thin wrapper over :mod:`omnia_api.services.reference_corpus` (where the reusable
comparator lives so V1.13b can wire it into the acceptance gauntlet). Renders a
candidate HTML file plus every curated reference in
``apps/api/tests/fixtures/reference/<niche>.html`` and prints a per-niche
meet-or-beat table over the five richness axes the taste/hierarchy gates already
score (type-scale, layout-variety, hero-imagery, focal-dominance, asymmetry).

Exit 1 if the candidate falls below the corpus on ANY niche (i.e. holds fewer
than ``--min-axes`` of the five axes). Money-free, 0 LLM.

    python scripts/compare_to_reference_corpus.py path/to/candidate.html
    python scripts/compare_to_reference_corpus.py --candidate tests/fixtures/bootstrap-baseline.html
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


async def _run(candidate_html: str, *, min_axes: int) -> int:
    comparisons = await rc.compare_to_corpus(candidate_html, min_axes=min_axes)
    if not comparisons:
        print(f"no reference corpus found in {rc.CORPUS_DIR}", file=sys.stderr)
        return 2

    any_abstain = False
    failed = False
    for c in comparisons:
        print(c.summary())
        if not c.rendered:
            any_abstain = True
        elif not c.passed:
            failed = True

    if any_abstain:
        print(
            "\nABSTAIN: no chromium available — run inside the prod-worker "
            "container for real teeth.",
            file=sys.stderr,
        )
        # Abstain is not a hard fail (R-10): a flaky/headless-less host must not
        # mint a green verdict, but it also must not block.
        return 0
    if failed:
        print("\nFAIL: candidate falls below the reference corpus.", file=sys.stderr)
        return 1
    print("\nOK: candidate meets or beats the reference corpus on every niche.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "candidate",
        nargs="?",
        help="path to the candidate HTML file to score against the corpus",
    )
    parser.add_argument(
        "--candidate",
        dest="candidate_opt",
        help="alternative way to pass the candidate path",
    )
    parser.add_argument(
        "--min-axes",
        type=int,
        default=rc.MIN_AXES,
        help=f"axes the candidate must hold ≥ reference (default {rc.MIN_AXES}/5)",
    )
    args = parser.parse_args(argv)

    path = args.candidate or args.candidate_opt
    if not path:
        parser.error("a candidate HTML path is required")
    candidate = Path(path)
    if not candidate.is_file():
        parser.error(f"candidate not found: {candidate}")

    html = candidate.read_text(encoding="utf-8")
    return asyncio.run(_run(html, min_axes=args.min_axes))


if __name__ == "__main__":
    raise SystemExit(main())
