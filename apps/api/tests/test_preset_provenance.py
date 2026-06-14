"""V1.10 cheap-core — deterministic coverage for the preset-provenance ratchet.

Everything here runs WITHOUT a browser, an LLM, the orchestrator, or the
network: the ratchet reads the static ``PRESETS`` declarations + the local
rule-table markdown. That is the contract — «вкус заморожен на снапшоте» must
be a machine-checkable fact, so a 30th preset that ships with an empty/cloned
``reference_url`` fails in CI, not in production.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

from omnia_api.services import preset_provenance as pp
from omnia_api.services.design_presets import PRESETS

# repo-root-relative path to the taste corpus doc (apps/api/tests → repo).
_RULE_TABLE = (
    Path(__file__).resolve().parents[3] / pp.RULE_TABLE_RELPATH
)


# ── CITATION ──────────────────────────────────────────────────────────────


def test_every_shipped_preset_cites_a_valid_reference() -> None:
    """The live ratchet: all 29 presets carry a structurally-valid source."""
    report = pp.evaluate(PRESETS)
    assert report.ok, f"provenance violations: {report.violations}"
    assert report.cited == report.total == len(PRESETS)


def test_missing_reference_is_flagged() -> None:
    pid = next(iter(PRESETS))
    broken = dict(PRESETS)
    broken[pid] = dataclasses.replace(PRESETS[pid], reference_url="")
    report = pp.evaluate(broken)
    assert not report.ok
    assert any(p == pid and r == "empty" for p, r in report.violations)


def test_malformed_reference_is_flagged() -> None:
    pid = next(iter(PRESETS))
    for bad in ("notaurl", "http://", "ftp://x.io", "https://x .com"):
        broken = dict(PRESETS)
        broken[pid] = dataclasses.replace(PRESETS[pid], reference_url=bad)
        report = pp.evaluate(broken)
        assert not report.ok, f"{bad!r} should be rejected"
        assert any(p == pid for p, _ in report.violations)


def test_check_reference_url_accepts_real_sources() -> None:
    for preset in PRESETS.values():
        assert pp.check_reference_url(preset.reference_url) is None


# ── DISTINCTNESS ──────────────────────────────────────────────────────────


def test_duplicate_reference_is_flagged() -> None:
    ids = list(PRESETS)
    a, b = ids[0], ids[1]
    shared = PRESETS[a].reference_url
    broken = dict(PRESETS)
    broken[b] = dataclasses.replace(PRESETS[b], reference_url=shared)
    report = pp.evaluate(broken)
    assert not report.ok
    flagged = {p for p, r in report.violations if r.startswith("duplicate-reference")}
    assert {a, b} <= flagged


def test_trailing_slash_does_not_defeat_distinctness() -> None:
    ids = list(PRESETS)
    a, b = ids[0], ids[1]
    broken = dict(PRESETS)
    broken[b] = dataclasses.replace(
        PRESETS[b], reference_url=PRESETS[a].reference_url.rstrip("/") + "/"
    )
    report = pp.evaluate(broken)
    assert not report.ok, "normalized duplicate must still collide"


# ── STALENESS ─────────────────────────────────────────────────────────────


def test_snapshot_date_is_iso_parseable() -> None:
    # raises ValueError if the constant ever drifts to a non-ISO string.
    parsed = date.fromisoformat(pp.TASTE_SNAPSHOT_DATE)
    assert parsed.year == 2026


def test_snapshot_constant_matches_rule_table_doc() -> None:
    """Drift guard: regenerating the taste corpus must bump BOTH the doc
    header and the code constant in lock-step."""
    doc_date = pp.read_rule_table_snapshot_date(_RULE_TABLE)
    assert doc_date is not None, f"no 'Сгенерено' marker in {_RULE_TABLE}"
    assert doc_date == pp.TASTE_SNAPSHOT_DATE


def test_snapshot_age_is_monotonic() -> None:
    assert pp.snapshot_age_days(date(2026, 6, 12)) == 0
    assert pp.snapshot_age_days(date(2026, 6, 22)) == 10


# ── COVERAGE-GAP ──────────────────────────────────────────────────────────


def test_covered_niche_emits_no_gap() -> None:
    assert pp.coverage_gap("Клиника МедЭлит", "стоматология приём пациентов") is False
    assert pp.coverage_gap("Магазин", "интернет-магазин товаров") is False


def test_uncovered_niche_emits_gap() -> None:
    assert pp.coverage_gap("zzqq plugh", "xyzzy frobnicate") is True
    assert pp.coverage_gap("", "") is True
