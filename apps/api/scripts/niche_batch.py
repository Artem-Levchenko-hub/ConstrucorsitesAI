#!/usr/bin/env python3
"""V1.6 6/5 — BATCH NICHE-RUNNER: the deterministic quality ratchet over a niche corpus.

The composition floor (taste + hierarchy, the heart of pillar 1) is asserted
per-generation by ``entity_gate`` / ``acceptance``. But there was no way to ask
the corpus-level question the ratchet actually needs: *"over N canonical niches,
each rendered at desktop AND mobile, what fraction passes — and did a new defect
class appear?"* This runner answers it with one JSON tally, so V1.6's flag-flip
(16/5e) and V1.7's pass-streak stop being eyeballed and become a number.

Two halves, cleanly split so the valuable half is deterministic and money-free:

  * SCORING (this module's teeth): given a live app URL per niche, resolve the
    WOW/content route (``route_target`` — never the bare ``/`` login wall, 16/5d),
    run the composition gauntlet at desktop (1440) AND mobile (390), and fold the
    verdicts into a JSON tally (pass-rate, per-niche min score, new-defect-class
    counter). Pure aggregation on top of the existing ``accept_gauntlet`` — no
    LLM, no generation, replayable on a fixed-seed corpus of running apps.

  * PROVISIONING (an injected seam): producing a *fresh generation* per niche is
    a paid LLM run — the owner-authorized 16/5e step, not something this runner
    fires on its own. So provisioning is a ``ProvisionFn`` you pass in. The
    shipped :func:`urls_provisioner` scores apps that are *already* running (the
    money-free path the CLI and 16/5e use: the owner generates the corpus once,
    hands their URLs here, the ratchet does the rest). A live-LLM provisioner is
    the 16/5e impl that plugs into the same seam.

Canon: R-01 (``score_app`` hides route-resolve + dual-width gauntlet + folding
behind one call), R-04 (reuses ``accept_gauntlet`` + ``route_target`` —
no duplicated gate or URL logic), R-10 (a provision/render miss degrades that run
to a fail, never crashes the batch).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the src-layout package importable when run as `python scripts/niche_batch.py`.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnia_api.services import accept_gauntlet, route_target  # noqa: E402
from omnia_api.services.chip_pixel_gate import FidelitySpec  # noqa: E402

#: The two viewports every niche is scored at — desktop composition + mobile.
DESKTOP_WIDTH = 1440
MOBILE_WIDTH = 390
DEFAULT_WIDTHS: tuple[int, ...] = (DESKTOP_WIDTH, MOBILE_WIDTH)


# ── The fixed-seed niche corpus ──────────────────────────────────────────────


@dataclass(frozen=True)
class Niche:
    """One canonical entity niche + the scripted discovery answers that drive it.

    The chip answers are *fixed* (no per-run randomness) so the corpus is
    replayable — the whole point of a ratchet. ``candidate_route`` is the content
    route ``route_target`` diverts to when ``/`` turns out to be a login wall
    (CRM / fintech); for marketing-landing niches ``/`` is already the WOW
    surface and the candidate is never used.
    """

    key: str
    prompt: str
    palette: str
    sections: tuple[str, ...]
    tone: str
    candidate_route: str = route_target.DEFAULT_CANDIDATE_ROUTE

    def spec(self) -> FidelitySpec:
        """Reify the scripted chip answers into a deterministic fidelity spec."""
        return FidelitySpec.from_answers(
            palette=self.palette, sections=list(self.sections), tone=self.tone
        )


#: The five canonical "good" entity niches of 16/5e (sushi / fitness / shop /
#: school / fintech). Order is fixed so ``--niches N`` always takes the same
#: prefix. sushi/fitness/shop/school are marketing-landing (``/`` is the WOW
#: surface); fintech is auth-gated (``/`` is a login wall → divert to /dashboard).
CORPUS: tuple[Niche, ...] = (
    Niche(
        key="sushi",
        prompt="Сайт службы доставки суши с меню, акциями и формой заказа.",
        palette="тёмная + красный",
        sections=("меню", "доставка", "контакты"),
        tone="premium",
    ),
    Niche(
        key="fitness",
        prompt="Сайт фитнес-студии: расписание занятий, тренеры, абонементы.",
        palette="тёмная + emerald",
        sections=("занятия", "тренеры", "цены", "контакты"),
        tone="energetic",
    ),
    Niche(
        key="shop",
        prompt="Интернет-магазин косметики с каталогом, отзывами и контактами.",
        palette="светлая + emerald",
        sections=("каталог", "отзывы", "контакты"),
        tone="friendly",
    ),
    Niche(
        key="school",
        prompt="Сайт частной школы: программы, преподаватели, приём, контакты.",
        palette="светлая + blue",
        sections=("программы", "преподаватели", "приём", "контакты"),
        tone="trustworthy",
    ),
    Niche(
        key="fintech",
        prompt="Финтех-приложение: дашборд, аналитика, карты, настройки.",
        palette="тёмная + violet",
        sections=("дашборд", "аналитика", "карты", "настройки"),
        tone="premium",
    ),
)


def select_niches(names: Sequence[str] | None, count: int | None) -> tuple[Niche, ...]:
    """Pick the niches to run: explicit ``names`` win, else the first ``count``.

    Unknown names raise — a typo silently scoring nothing is worse than a stop.
    """
    if names:
        by_key = {n.key: n for n in CORPUS}
        missing = [nm for nm in names if nm not in by_key]
        if missing:
            raise ValueError(f"unknown niche(s): {', '.join(missing)}")
        return tuple(by_key[nm] for nm in names)
    if count is not None:
        return CORPUS[:count]
    return CORPUS


# ── Provisioning seam ────────────────────────────────────────────────────────

#: Produce the live base URL (``http://omnia-dev-<slug>:3000``) for one run of a
#: niche, or ``None`` if the app could not be made ready. A *fresh-generation*
#: provisioner (paid LLM, the 16/5e step) and a *score-existing* provisioner both
#: satisfy this shape.
ProvisionFn = Callable[[Niche, int], Awaitable[str | None]]


def urls_provisioner(urls: dict[str, str]) -> ProvisionFn:
    """Score apps that are ALREADY running — the money-free path.

    ``urls`` maps niche key → base container URL. No generation, no LLM: the
    owner provisions the corpus once (16/5e) and hands the URLs here.
    """

    async def _provision(niche: Niche, _run_index: int) -> str | None:
        return urls.get(niche.key)

    return _provision


# ── Scoring one app ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WidthResult:
    """The composition verdict at one viewport."""

    width: int
    passed: bool
    failed_classes: tuple[str, ...]
    gate_score: float  # passed gates / total gates (the "n/5" the gauntlet ran)
    subscore: dict[str, Any]


@dataclass(frozen=True)
class RunResult:
    """One niche × one run: its verdict at every scored viewport."""

    niche: str
    run_index: int
    base_url: str | None
    route: str
    widths: tuple[WidthResult, ...]

    @property
    def passed(self) -> bool:
        """A run passes only if every viewport passed (and at least one ran)."""
        return bool(self.widths) and all(w.passed for w in self.widths)

    @property
    def min_score(self) -> float:
        """Worst viewport score — an empty (provision-failed) run scores 0."""
        return min((w.gate_score for w in self.widths), default=0.0)

    @property
    def failed_classes(self) -> tuple[str, ...]:
        """De-duplicated union of failing gate classes across viewports."""
        seen: dict[str, None] = {}
        for w in self.widths:
            for c in w.failed_classes:
                seen.setdefault(c, None)
        return tuple(seen)


def _compose_url(base: str, route: str) -> str:
    b = base.rstrip("/")
    return b + "/" if not route or route == "/" else b + route


def _width_result(width: int, verdict: accept_gauntlet.GauntletVerdict) -> WidthResult:
    gates = verdict.gates
    passed_gates = sum(1 for g in gates if g.passed)
    score = passed_gates / len(gates) if gates else 0.0
    return WidthResult(
        width=width,
        passed=verdict.passed,
        failed_classes=verdict.failed_classes,
        gate_score=score,
        subscore=verdict.subscore(),
    )


async def score_app(
    base_url: str,
    niche: Niche,
    *,
    run_index: int = 0,
    widths: Sequence[int] = DEFAULT_WIDTHS,
    gauntlet_run: Callable[..., Awaitable[accept_gauntlet.GauntletVerdict]] | None = None,
    resolve_route: Callable[..., Awaitable[str]] | None = None,
) -> RunResult:
    """Resolve the WOW route, then run the composition gauntlet at every width.

    R-01: one call hides route-targeting + dual-width gauntlet + folding. The
    gauntlet legs (taste + hierarchy, ``composition=True`` / ``include_rendered=
    False``) are exactly the awwwards floor the entity gate ships. ``gauntlet_run``
    / ``resolve_route`` default to the live functions but are resolved at call
    time, so a test (or the CLI) can monkeypatch the module attribute.
    """
    gauntlet_run = gauntlet_run or accept_gauntlet.run
    resolve_route = resolve_route or route_target.resolve_target_route
    route = await resolve_route(base_url, candidate_route=niche.candidate_route)
    url = _compose_url(base_url, route)
    spec = niche.spec()
    results: list[WidthResult] = []
    for w in widths:
        # Drive BOTH the correctness floor AND the composition legs at this
        # viewport: at MOBILE_WIDTH the composition legs (taste + hierarchy) now
        # render at 390 too (V1.6 15/5), so a desktop-rich / mobile-monotone app
        # fails the @390 pass instead of silently re-scoring the desktop render.
        verdict = await gauntlet_run(
            url=url,
            spec=spec,
            width=w,
            composition_width=w,
            composition=True,
            include_rendered=False,
        )
        results.append(_width_result(w, verdict))
    return RunResult(niche.key, run_index, base_url, route, tuple(results))


async def run_niche(
    niche: Niche, runs: int, provision: ProvisionFn, **score_kw: Any
) -> list[RunResult]:
    out: list[RunResult] = []
    for i in range(runs):
        base = await provision(niche, i)
        if base is None:  # provision miss → a scored fail, never a crash (R-10)
            out.append(RunResult(niche.key, i, None, "/", ()))
            continue
        out.append(await score_app(base, niche, run_index=i, **score_kw))
    return out


async def run_batch(
    niches: Sequence[Niche], runs: int, provision: ProvisionFn, **score_kw: Any
) -> list[RunResult]:
    results: list[RunResult] = []
    for niche in niches:
        results.extend(await run_niche(niche, runs, provision, **score_kw))
    return results


# ── Tally (pure) ─────────────────────────────────────────────────────────────


def tally(
    results: Sequence[RunResult],
    *,
    niches: int,
    runs_per_niche: int,
    known_classes: Sequence[str] = (),
) -> dict[str, Any]:
    """Fold per-run verdicts into the JSON ratchet report.

    ``known_classes`` is the baseline registry of defect classes already seen;
    anything outside it is a NEW class — the signal that the floor regressed.
    """
    known = set(known_classes)
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    per_niche: dict[str, dict[str, Any]] = {}
    all_classes: set[str] = set()
    for r in results:
        agg = per_niche.setdefault(
            r.niche,
            {"runs": 0, "passed": 0, "min_score": 1.0, "failed_classes": set()},
        )
        agg["runs"] += 1
        agg["passed"] += int(r.passed)
        agg["min_score"] = min(agg["min_score"], r.min_score)
        agg["failed_classes"].update(r.failed_classes)
        all_classes.update(r.failed_classes)

    per_niche_out = {
        key: {
            "runs": v["runs"],
            "passed": v["passed"],
            "pass_rate": round(v["passed"] / v["runs"], 4) if v["runs"] else 0.0,
            "min_score": round(v["min_score"] if v["runs"] else 0.0, 4),
            "failed_classes": sorted(v["failed_classes"]),
        }
        for key, v in per_niche.items()
    }
    new_classes = sorted(all_classes - known)

    return {
        "niches": niches,
        "runs_per_niche": runs_per_niche,
        "total_runs": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "all_passed": total > 0 and passed == total,
        "per_niche": per_niche_out,
        "defect_classes": sorted(all_classes),
        "new_defect_classes": new_classes,
        "new_defect_class_count": len(new_classes),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="niche_batch",
        description="Score running niche apps and emit the V1.6 ratchet JSON tally.",
    )
    p.add_argument(
        "--urls",
        required=True,
        help="JSON file mapping niche key -> base container URL of an ALREADY "
        "running app (fresh-generation provisioning is the owner-authorized "
        "16/5e step; this runner scores, it does not generate).",
    )
    p.add_argument("--niches", type=int, default=None, help="Score the first N corpus niches.")
    p.add_argument("--only", default=None, help="Comma-separated niche keys to score.")
    p.add_argument("--runs", type=int, default=1, help="Runs (re-scores) per niche.")
    p.add_argument(
        "--known-classes",
        default=None,
        help="JSON array of baseline defect classes; classes outside it are NEW.",
    )
    p.add_argument("--out", default=None, help="Also write the JSON tally to this path.")
    return p.parse_args(list(argv))


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    names = [s.strip() for s in args.only.split(",")] if args.only else None
    niches = select_niches(names, args.niches)
    urls = _load_json(args.urls)
    known = _load_json(args.known_classes) if args.known_classes else ()
    provision = urls_provisioner(urls)
    results = await run_batch(niches, args.runs, provision)
    return tally(
        results,
        niches=len(niches),
        runs_per_niche=args.runs,
        known_classes=known,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = asyncio.run(_run_cli(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    # exit 0 iff every scored run passed — the gate a CI / flip-check asserts on.
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover — thin CLI wrapper
    raise SystemExit(main())
