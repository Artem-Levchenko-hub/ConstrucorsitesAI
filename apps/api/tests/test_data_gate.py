"""Non-empty-data gate — the seeded catalog must never render empty (V1.6 5/5).

The rubric is browser-free: :func:`evaluate_observation` scores a hand-built
observation dict (exactly what ``_AUDIT_JS`` returns), so every threshold is
tested with no chromium. One render test exercises the committed adversarial
fixtures for real, abstaining (skipping) when no browser is present.
"""

import asyncio
from pathlib import Path

import pytest

from omnia_api.services import data_gate as g
from omnia_api.services.data_gate import (
    EMPTY_COLLECTION,
    MIN_ROWS,
    THIN_COLLECTION,
    DataReport,
    evaluate_observation,
)


def _obs(*counts, visible=True):
    """Build an observation with one collection per given row count."""
    return {"collections": [{"rows": str(c), "visible": visible} for c in counts]}


# ── pass cases ────────────────────────────────────────────────────────────────


def test_full_catalog_passes():
    rep = evaluate_observation(_obs(MIN_ROWS))
    assert rep.passed is True
    assert rep.classes == ()
    assert rep.collections == 1


def test_well_filled_catalog_passes():
    rep = evaluate_observation(_obs(7, 12, 9))
    assert rep.passed is True
    assert rep.collections == 3


def test_no_collection_passes_not_this_gates_concern():
    # a marketing landing with no data surface must not be penalised
    rep = evaluate_observation({"collections": []})
    assert rep.passed is True
    assert rep.collections == 0
    assert "no record collection" in rep.summary()


def test_boundary_exactly_min_rows_passes():
    rep = evaluate_observation(_obs(MIN_ROWS))
    assert rep.passed is True


# ── fail cases ──────────────────────────────────────────────────────────────


def test_empty_catalog_fails():
    rep = evaluate_observation(_obs(0))
    assert rep.passed is False
    assert EMPTY_COLLECTION in rep.classes
    assert THIN_COLLECTION not in rep.classes


def test_thin_catalog_fails():
    rep = evaluate_observation(_obs(MIN_ROWS - 1))
    assert rep.passed is False
    assert THIN_COLLECTION in rep.classes
    assert EMPTY_COLLECTION not in rep.classes


def test_boundary_one_below_floor_is_thin():
    rep = evaluate_observation(_obs(5))
    assert rep.passed is False
    assert THIN_COLLECTION in rep.classes


def test_one_full_one_empty_fails_on_the_empty_one():
    rep = evaluate_observation(_obs(8, 0))
    assert rep.passed is False
    assert EMPTY_COLLECTION in rep.classes


def test_both_classes_can_fire_together():
    rep = evaluate_observation(_obs(0, 2))
    assert rep.passed is False
    assert set(rep.classes) == {EMPTY_COLLECTION, THIN_COLLECTION}


def test_unparseable_row_count_reads_as_empty():
    rep = evaluate_observation({"collections": [{"rows": "—", "visible": True}]})
    assert rep.passed is False
    assert EMPTY_COLLECTION in rep.classes


def test_missing_row_attribute_reads_as_empty():
    rep = evaluate_observation({"collections": [{"visible": True}]})
    assert rep.passed is False
    assert EMPTY_COLLECTION in rep.classes


# ── visibility ────────────────────────────────────────────────────────────────


def test_hidden_collection_is_ignored():
    # a collapsed/hidden tab's empty table must not sink a page whose visible
    # catalog is fine
    rep = evaluate_observation(
        {
            "collections": [
                {"rows": "8", "visible": True},
                {"rows": "0", "visible": False},
            ]
        }
    )
    assert rep.passed is True
    assert rep.collections == 1


# ── abstain / report surface ──────────────────────────────────────────────────


def test_not_rendered_abstains():
    rep = evaluate_observation({"collections": [{"rows": "0", "visible": True}]}, rendered=False)
    assert rep.rendered is False
    assert rep.passed is False  # abstain ≠ pass
    assert "ABSTAIN" in rep.summary()


def test_subscore_is_machine_readable():
    rep = evaluate_observation(_obs(0))
    sub = rep.subscore()
    assert sub["gate"] == "data"
    assert sub["passed"] is False
    assert sub["min_rows"] == MIN_ROWS
    assert EMPTY_COLLECTION in sub["classes"]
    assert sub["collections"] == 1


def test_detail_reports_min_row_count():
    rep = evaluate_observation(_obs(9, 6, 7))
    assert rep.detail["min_row_count"] == 6
    assert rep.detail["row_counts"] == [9, 6, 7]


def test_min_rows_tracks_the_seeder_floor():
    # cross-service contract: the gate floor MUST equal the seeder's floor.
    # (separate packages — assert the number a reader would expect, so a drift
    # in either side trips this test.)
    assert MIN_ROWS == 6


# ── the browser-dependent guarantee: committed fixtures render as expected ─────

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_empty_catalog_fixture_renders_red():
    """Render the committed empty-catalog fixture for real. Abstains (skips) when
    no chromium is available locally; runs with teeth in the prod-worker container."""
    html = (_FIXTURES / "empty-catalog.html").read_text(encoding="utf-8")
    rep = asyncio.run(g.audit_files({"index.html": html}))
    if not rep.rendered:
        pytest.skip("no chromium available — verified in prod-worker container")
    assert rep.passed is False, f"empty catalog must fail data gate: {rep.summary()}"
    assert EMPTY_COLLECTION in rep.classes


def test_full_catalog_fixture_renders_green():
    """The committed seeded-catalog fixture renders ≥ MIN_ROWS rows → passes."""
    html = (_FIXTURES / "full-catalog.html").read_text(encoding="utf-8")
    rep = asyncio.run(g.audit_files({"index.html": html}))
    if not rep.rendered:
        pytest.skip("no chromium available — verified in prod-worker container")
    assert rep.passed is True, f"seeded catalog must pass data gate: {rep.summary()}"
    assert rep.collections == 1


def test_files_audit_without_index_abstains():
    rep = asyncio.run(g.audit_files({"styles.css": "body{}"}))
    assert isinstance(rep, DataReport)
    assert rep.rendered is False
