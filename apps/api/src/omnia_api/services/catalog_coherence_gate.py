"""Catalog-realism gate — the RULE-10 demo-seeder wave becomes a permanent floor (V1.17).

Eight money-free RULE-10 fixes landed in the orchestrator's ``demo_seeder.py``
(niche-aware titles, price-bands, real images, title↔category, title↔description,
category synonyms, future dates, niche emails) and each shipped with unit tests.
But the *only* gate that touches the seeder's output, ``data_gate.py``, measures
``MIN_ROWS >= 6`` — non-emptiness, never realism. So a NEW niche can silently
regress any of the eight classes (a vitamin priced 197010₽, a "Витамин C" filed
under "Косметика", a broken ``<img>``, an "акция до" in the past) and nothing
catches it: the wave is eight one-offs, not a ratchet.

This module is that ratchet. It scores the *rendered* catalog DOM — the actual
surface a user sees — across five realism axes, mirroring ``taste_gate``: the
injected ``_AUDIT_JS`` does only DOM extraction (catalog rows → title / category /
description / price / image-state / promo-date), and every threshold and verdict
lives in pure Python (:func:`evaluate_observation`), so the whole rubric is
unit-testable with a hand-built dict — no browser, no money, no flake. Each future
RULE-10 defect class becomes one more axis here (a ratchet, not a prose TODO).

The five realism axes (each worth one point; clean catalog == 5/5, 0 findings)
============================================================================
  1. ``price-band``        — no non-positive price and no absurd intra-catalog
                             outlier (a 197010₽ item among ~1490₽ siblings). The
                             check is niche-AGNOSTIC: it reads the catalog's own
                             median, so a real-estate catalog priced in millions
                             is self-consistent and passes, while one wild item
                             fails — no niche taxonomy import needed.
  2. ``title-category``    — every row's title coheres with its category: a row
                             whose title shares vocabulary with a SIBLING category
                             more than its own (a "Витамин C" shelved under
                             "Косметика" while the other vitamins sit under
                             "Витамины") is flagged as miscategorized.
  3. ``title-description`` — every titled row carries a real, non-trivial
                             description, and no single description is copy-pasted
                             across a large share of the catalog (the seeder tell
                             of generic praise).
  4. ``image-resolves``    — no row renders a broken / empty / unresolved
                             ``<img>`` (the extractor flags an image element whose
                             src is empty, a placeholder marker, or failed to
                             decode).
  5. ``date-future``       — no promo / "акция до" date sits in the past relative
                             to the render's own clock.

Non-blocking by design (V1.17). The acceptance gauntlet folds this in as a
quality CARD (score 0–5, observation), not a hard ship-block: it teaches the
ratchet without false-rejecting good catalogs while the niche heuristics earn
trust. A page with no catalog WAIVES (the wrong surface to score), exactly like
``taste_gate`` waives a login page.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render_settle import goto_and_settle

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_AUDIT_JS`` and scored by Python. The shape is
# pinned by the JS extractor and exercised by the unit tests, not the type system.
Obs = dict[str, Any]

# Catalog realism is a content concern best read where the grid breathes; the
# gauntlet fans this at desktop width like taste.
GATE_WIDTH = 1440
GATE_HEIGHT = 900

# A catalog must clear ALL five axes to count as clean (passed). The gate is
# non-blocking, so "passed" is the pristine-observation signal, not a ship-stop.
MIN_SCORE = 5

# ── thresholds (the testable knobs) ───────────────────────────────────────────
# Fewer rows than this and there is no catalog to judge — the report WAIVES
# (wrong surface) rather than scoring an empty grid 5/5.
_MIN_CATALOG_ROWS = 2
# A price is an absurd outlier when it towers over the catalog's own median by
# more than this multiple. Niche-agnostic: the median rides the niche, so a
# millions-priced real-estate catalog is self-consistent; one wild item is not.
_PRICE_OUTLIER_RATIO = 25.0
# The outlier check needs a stable median — at least this many priced rows.
_MIN_PRICED_ROWS = 4
# title↔category coherence only runs with enough categorised rows to compare …
_MIN_CATEGORISED_ROWS = 4
# … and at least this many distinct categories (else there is nothing to be
# mis-filed BETWEEN).
_MIN_DISTINCT_CATEGORIES = 2
# A sibling category must have at least this many members before a row's stronger
# affinity to it reads as a real mis-filing (not a one-row coincidence).
_MIN_SIBLING_CATEGORY_SIZE = 2
# Title tokens shorter than this, or pure digits, carry no category signal.
_MIN_TOKEN_LEN = 3
# A description shorter than this (after trim) is not a real product blurb.
_MIN_DESCRIPTION_LEN = 8
# One identical description copy-pasted across more than this fraction of the
# catalog is the generic-praise seeder tell.
_MAX_DESCRIPTION_SHARE = 0.5
# The repetition check only bites once the catalog is big enough that sharing is
# a tell rather than a coincidence of a tiny grid.
_MIN_ROWS_FOR_REPETITION = 4
# Placeholder / unresolved image markers the extractor reports as broken.
_PLACEHOLDER_IMAGE_TOKENS = ("data-omnia-gen", "placeholder")

# Stopwords with no category signal (units, generic product words). Kept tiny and
# domain-neutral — the check leans on positive sibling-overlap, not this list.
_TITLE_STOPWORDS = frozenset(
    {
        "для",
        "под",
        "при",
        "без",
        "the",
        "for",
        "with",
        "and",
        "про",
        "набор",
        "комплект",
        "set",
        "pro",
        "plus",
        "макс",
        "max",
        "mini",
        "мини",
    }
)

# Check ids — the vocabulary of the subscore.
PRICE_BAND = "price-band"
TITLE_CATEGORY = "title-category"
TITLE_DESCRIPTION = "title-description"
IMAGE_RESOLVES = "image-resolves"
DATE_FUTURE = "date-future"

CHECKS: tuple[str, ...] = (
    PRICE_BAND,
    TITLE_CATEGORY,
    TITLE_DESCRIPTION,
    IMAGE_RESOLVES,
    DATE_FUTURE,
)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogFinding:
    """One realism defect on one catalog row."""

    check: str
    detail: str


@dataclass(frozen=True)
class CatalogReport:
    """Verdict + JSON subscore of one catalog-realism audit.

    Shares the rendered-gate interface (``passed`` / ``rendered`` / ``classes`` /
    ``summary`` / ``subscore``) so the acceptance gauntlet folds it in through the
    same adapter as the taste / wow-dom / chip gates.
    """

    findings: tuple[CatalogFinding, ...]
    score: int
    viewport_width: int
    row_count: int
    rendered: bool
    detail: dict[str, Any] = field(default_factory=dict)
    #: ``"catalog"`` for a real product/listing grid; ``"none"`` when the page
    #: carries no catalog, in which case the realism rubric is WAIVED (the wrong
    #: surface to score) rather than scored 5/5 on an empty grid.
    surface: str = "catalog"

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence.

        A ``none`` surface PASSES as waived: there is no catalog to be unreal.
        """
        if not self.rendered:
            return False
        if self.surface == "none":
            return True
        return self.score >= MIN_SCORE

    @property
    def classes(self) -> tuple[str, ...]:
        """The failed checks, in canonical order (what the gauntlet table shows)."""
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        """Machine-readable subscore — emitted into the gauntlet's JSON."""
        return {
            "gate": "catalog",
            "rendered": self.rendered,
            "passed": self.passed,
            "score": self.score,
            "max_score": len(CHECKS),
            "viewport_width": self.viewport_width,
            "row_count": self.row_count,
            "surface": self.surface,
            "checks": {c: c not in self.classes for c in CHECKS},
            "findings": [{"check": f.check, "detail": f.detail} for f in self.findings],
            "detail": self.detail,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "catalog: ABSTAIN (render harness did not run)"
        if self.surface == "none":
            return "catalog: WAIVED (no catalog grid on page — realism rubric N/A)"
        if self.passed:
            return f"catalog: {self.score}/{len(CHECKS)} realism axes clean ({self.row_count} rows)"
        lines = [
            f"catalog: {self.score}/{len(CHECKS)} realism axes "
            f"({self.row_count} rows) — {len(self.findings)} defect(s):"
        ]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── helpers (pure) ─────────────────────────────────────────────────────────────


def _rows(obs: Obs) -> list[dict[str, Any]]:
    rows = obs.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _prices(rows: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """``(row_index, price)`` for every row carrying a numeric price."""
    out: list[tuple[int, float]] = []
    for i, r in enumerate(rows):
        p = r.get("price")
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            out.append((i, float(p)))
    return out


def title_tokens(title: str | None) -> set[str]:
    """Category-bearing tokens of a title: lowercased words ≥ 3 chars, no digits,
    no units/stopwords. ``"Витамин C 900 мг"`` → ``{"витамин"}``."""
    if not title:
        return set()
    raw = re.split(r"[^0-9A-Za-zЀ-ӿ]+", title.lower())
    return {
        t
        for t in raw
        if len(t) >= _MIN_TOKEN_LEN and not t.isdigit() and t not in _TITLE_STOPWORDS
    }


def _parse_date(value: Any) -> date | None:
    """Lenient ISO-ish date parse: ``2026-08-15`` or ``15.08.2026`` → ``date``."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
        if not m:
            return None
        d, mo, y = (int(g) for g in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


# ── the five realism checks (each returns a finding list — empty == passed) ────


def _score_price_band(rows: list[dict[str, Any]]) -> list[CatalogFinding]:
    priced = _prices(rows)
    findings: list[CatalogFinding] = []
    for i, p in priced:
        if p <= 0:
            findings.append(
                CatalogFinding(
                    PRICE_BAND,
                    f"row {i} ({_label(rows[i])}) priced {p:g}₽ — non-positive price",
                )
            )
    if findings:
        return findings
    if len(priced) < _MIN_PRICED_ROWS:
        return []
    values = [p for _, p in priced]
    median = statistics.median(values)
    if median <= 0:
        return []
    for i, p in priced:
        if p > median * _PRICE_OUTLIER_RATIO:
            findings.append(
                CatalogFinding(
                    PRICE_BAND,
                    f"row {i} ({_label(rows[i])}) priced {p:g}₽ — "
                    f"{p / median:.0f}× the catalog median {median:g}₽ (absurd outlier)",
                )
            )
    return findings


def _score_title_category(rows: list[dict[str, Any]]) -> list[CatalogFinding]:
    indexed = [
        (i, title_tokens(_get(r, "title")), str(_get(r, "category") or "").strip())
        for i, r in enumerate(rows)
    ]
    categorised = [(i, t, c) for i, t, c in indexed if c and t]
    cats = {c for _, _, c in categorised}
    if len(categorised) < _MIN_CATEGORISED_ROWS or len(cats) < _MIN_DISTINCT_CATEGORIES:
        return []
    by_cat: dict[str, list[tuple[int, set[str]]]] = {}
    for i, t, c in categorised:
        by_cat.setdefault(c, []).append((i, t))

    findings: list[CatalogFinding] = []
    for i, tokens, c in categorised:
        own = max(
            (len(tokens & other) for j, other in by_cat[c] if j != i),
            default=0,
        )
        best_other = 0
        best_cat = ""
        for oc, members in by_cat.items():
            if oc == c or len(members) < _MIN_SIBLING_CATEGORY_SIZE:
                continue
            overlap = max((len(tokens & other) for _, other in members), default=0)
            if overlap > best_other:
                best_other, best_cat = overlap, oc
        if best_other >= 1 and best_other > own:
            findings.append(
                CatalogFinding(
                    TITLE_CATEGORY,
                    f"row {i} ({_label(rows[i])}) filed under «{c}» but its title "
                    f"matches «{best_cat}» more closely — miscategorised",
                )
            )
    return findings


def _score_title_description(rows: list[dict[str, Any]]) -> list[CatalogFinding]:
    findings: list[CatalogFinding] = []
    descs: list[str] = []
    for i, r in enumerate(rows):
        title = _get(r, "title")
        if not (isinstance(title, str) and title.strip()):
            continue
        desc = _get(r, "description")
        text = desc.strip() if isinstance(desc, str) else ""
        if len(text) < _MIN_DESCRIPTION_LEN:
            findings.append(
                CatalogFinding(
                    TITLE_DESCRIPTION,
                    f"row {i} ({_label(r)}) has no real description "
                    f"({len(text)} chars) — bare title",
                )
            )
        else:
            descs.append(text.lower())
    if findings:
        return findings
    n = len(descs)
    if n >= _MIN_ROWS_FOR_REPETITION:
        for text in set(descs):
            share = descs.count(text) / n
            if share > _MAX_DESCRIPTION_SHARE:
                findings.append(
                    CatalogFinding(
                        TITLE_DESCRIPTION,
                        f"one description repeats across {share:.0%} of rows "
                        f"(> {_MAX_DESCRIPTION_SHARE:.0%}) — generic copy, not per-item",
                    )
                )
                break
    return findings


def _score_image_resolves(rows: list[dict[str, Any]]) -> list[CatalogFinding]:
    findings: list[CatalogFinding] = []
    for i, r in enumerate(rows):
        if _is_image_broken(r):
            findings.append(
                CatalogFinding(
                    IMAGE_RESOLVES,
                    f"row {i} ({_label(r)}) renders a broken/empty image "
                    "— src missing, placeholder, or failed to decode",
                )
            )
    return findings


def _score_date_future(rows: list[dict[str, Any]], today: date) -> list[CatalogFinding]:
    findings: list[CatalogFinding] = []
    for i, r in enumerate(rows):
        when = _parse_date(_get(r, "promoDate") or _get(r, "promo_until"))
        if when is not None and when < today:
            findings.append(
                CatalogFinding(
                    DATE_FUTURE,
                    f"row {i} ({_label(r)}) promo date {when.isoformat()} "
                    f"is in the past (today {today.isoformat()}) — expired on screen",
                )
            )
    return findings


# ── row-field accessors (tolerant of the extractor's flat dict) ────────────────


def _get(row: dict[str, Any], key: str) -> Any:
    return row.get(key)


def _label(row: dict[str, Any]) -> str:
    title = _get(row, "title")
    return str(title).strip()[:40] if isinstance(title, str) and title.strip() else "untitled"


def _is_image_broken(row: dict[str, Any]) -> bool:
    """A row's image is broken when the extractor flagged it, or its src is empty
    / a placeholder marker. Rows with no image element at all are NOT broken —
    the gate judges realism of present images, not their presence (taste owns
    hero imagery)."""
    if not row.get("hasImage"):
        return False
    if row.get("imageBroken"):
        return True
    src = row.get("imageSrc")
    if isinstance(src, str):
        s = src.strip()
        if not s:
            return True
        if any(tok in s for tok in _PLACEHOLDER_IMAGE_TOKENS):
            return True
    return False


def evaluate_observation(
    obs: Obs, *, rendered: bool = True, today: date | None = None
) -> CatalogReport:
    """Score a raw DOM observation dict → :class:`CatalogReport`.

    This is the whole rubric, browser-free. ``obs`` is exactly what ``_AUDIT_JS``
    returns; passing a hand-built dict is how the gate is unit-tested. ``today``
    pins the clock for the date-future axis (defaults to ``obs['now']``), keeping
    the check deterministic in tests.
    """
    vw = int(obs.get("viewportWidth") or GATE_WIDTH)
    rows = _rows(obs)
    if not rendered:
        return CatalogReport((), 0, vw, 0, rendered=False)

    # No catalog to judge — waive (wrong surface), don't score an empty grid 5/5.
    if len(rows) < _MIN_CATALOG_ROWS:
        return CatalogReport(
            (), len(CHECKS), vw, len(rows), rendered=True, surface="none",
            detail={"surface": "none", "rows": len(rows)},
        )

    ref = today or _parse_date(obs.get("now")) or date.today()

    findings: list[CatalogFinding] = []
    findings += _score_price_band(rows)
    findings += _score_title_category(rows)
    findings += _score_title_description(rows)
    findings += _score_image_resolves(rows)
    findings += _score_date_future(rows, ref)

    failed = {f.check for f in findings}
    score = sum(1 for c in CHECKS if c not in failed)
    detail = {
        "rows": len(rows),
        "priced_rows": len(_prices(rows)),
        "categories": len({str(_get(r, "category") or "").strip() for r in rows} - {""}),
        "reference_date": ref.isoformat(),
    }
    return CatalogReport(tuple(findings), score, vw, len(rows), rendered=True, detail=detail)


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Returns the raw observation scored by evaluate_observation(). It reads catalog
# rows; it never judges. A "row" is a repeated card-like container that carries a
# price — the structural tell of a product / listing grid. Per row it extracts a
# title (the most prominent text), a category, a description, the numeric price,
# the image state, and any promo/expiry date text.
_AUDIT_JS = r"""
() => {
  const txt = (el) => (el && el.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  // A price token: ₽ / руб / "от 1 490" — Russian thousands use spaces.
  const PRICE_RE = /(\d[\d  .]{0,12}\d|\d)\s*(?:₽|руб|р\.|rub)/i;
  const parsePrice = (s) => {
    const m = (s || '').match(PRICE_RE);
    if (!m) return null;
    const n = parseFloat(m[1].replace(/[  .]/g, ''));
    return Number.isFinite(n) ? n : null;
  };
  const DATE_RE = /(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})/;

  // Find the repeated catalog container: the deepest element whose direct
  // children are ≥3 sibling cards that each contain a price. Scan candidates
  // and keep the one with the most price-bearing children.
  let best = null, bestCount = 0;
  document.querySelectorAll('ul, ol, div, section, main, tbody').forEach((box) => {
    if (!visible(box)) return;
    const kids = Array.from(box.children).filter(visible);
    if (kids.length < 3) return;
    const priced = kids.filter((k) => PRICE_RE.test(txt(k)));
    if (priced.length >= 3 && priced.length > bestCount) {
      best = box; bestCount = priced.length;
    }
  });
  if (!best) return { viewportWidth: document.documentElement.clientWidth, rows: [] };

  const cards = Array.from(best.children).filter((k) => visible(k) && PRICE_RE.test(txt(k)));
  const rows = cards.slice(0, 60).map((card) => {
    // title — first heading, else the longest short text line.
    let title = '';
    const h = card.querySelector('h1,h2,h3,h4,h5,[class*="title" i],[class*="name" i]');
    if (h && txt(h)) title = txt(h);
    if (!title) {
      const lines = Array.from(card.querySelectorAll('*'))
        .map(txt).filter((s) => s && s.length <= 60);
      title = lines.sort((a, b) => b.length - a.length)[0] || txt(card).slice(0, 60);
    }
    const cat = card.querySelector(
      '[class*="categ" i],[class*="rubric" i],[class*="tag" i],[data-category]');
    const desc = card.querySelector(
      '[class*="desc" i],[class*="about" i],[class*="excerpt" i],p');
    const img = card.querySelector('img');
    const full = txt(card);
    return {
      title,
      category: cat ? txt(cat) : (card.getAttribute && card.getAttribute('data-category')) || '',
      description: desc ? txt(desc) : '',
      price: parsePrice(full),
      hasImage: !!img,
      imageSrc: img ? (img.getAttribute('src') || '') : '',
      imageBroken: img ? (img.complete && img.naturalWidth === 0) : false,
      promoDate: (full.match(DATE_RE) || [null])[0],
    };
  });

  return {
    viewportWidth: document.documentElement.clientWidth,
    now: new Date().toISOString().slice(0, 10),
    rows,
  };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page) -> CatalogReport:
    obs = await page.evaluate(_AUDIT_JS)
    return evaluate_observation(obs)


async def audit_url(
    url: str, *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> CatalogReport:
    """Audit a LIVE url (a running container app / prod ``/p/<slug>``) at ``width``.

    Fail-soft: any render/navigation error → an ABSTAIN report (``rendered=False``)
    rather than a raise, so a flaky container never hard-fails the gauntlet (R-10).
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    viewport={"width": int(width), "height": GATE_HEIGHT},
                    reduced_motion="reduce",
                )
                try:
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    return await _audit_page(page)
                finally:
                    await page.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("catalog_coherence_gate: url audit failed (abstain): %r", exc)
        return CatalogReport((), 0, int(width), 0, rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> CatalogReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html)."""
    if "index.html" not in files:
        return CatalogReport((), 0, int(width), 0, rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-catalog-") as tmp:
            workdir = Path(tmp)
            for path, content in files.items():
                full = workdir / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
            index_uri = (workdir / "index.html").as_uri()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        viewport={"width": int(width), "height": GATE_HEIGHT},
                        reduced_motion="reduce",
                    )
                    try:
                        await goto_and_settle(page, index_uri, timeout_ms=timeout_ms)
                        return await _audit_page(page)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
    except Exception as exc:
        log.warning("catalog_coherence_gate: files audit failed (abstain): %r", exc)
        return CatalogReport((), 0, int(width), 0, rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.catalog_coherence_gate <url|index.html-dir>")
        return 2
    target = argv[1]
    if target.startswith(("http://", "https://")):
        report = asyncio.run(audit_url(target))
    else:
        root = Path(target)
        files = {
            str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in root.rglob("*.html")
        }
        report = asyncio.run(audit_files(files))
    print(report.summary())
    print(json.dumps(report.subscore(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "CHECKS",
    "GATE_WIDTH",
    "MIN_SCORE",
    "CatalogFinding",
    "CatalogReport",
    "audit_files",
    "audit_url",
    "evaluate_observation",
    "title_tokens",
]
