"""P00: the performance baseline is honest — only QA projects are measured,
missing timings never become zero, the mode follows the journal state, and the
recorded baseline document is internally consistent."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import pytest

from tests.publication_perf_fixture import (
    MODES,
    ForeignProjectError,
    load_baseline,
    parse_timings,
    profile,
    publication_mode,
    qa_project_ids,
)

QA = UUID("b958d03c-38c0-4374-8a47-3f06cb721096")


def test_user_projects_are_refused():
    with pytest.raises(ForeignProjectError):
        profile(uuid4(), {"data_seeded": True}, desired_snapshot_id="x", logs=[])
    assert QA in qa_project_ids()


def test_missing_timings_stay_unknown_not_zero():
    assert parse_timings(None) == parse_timings([]) == parse_timings(["something else"])
    assert parse_timings([]).prepare_ms is None
    partial = parse_timings(["prepare_ms=66545"])
    assert (partial.prepare_ms, partial.activate_ms, partial.total_ms) == (66545, None, None)
    full = parse_timings(["prepare_ms=66545", "activate_ms=47604", "total_ms=114149"])
    assert (full.prepare_ms, full.activate_ms, full.total_ms) == (66545, 47604, 114149)


def test_mode_comes_from_journal_state_not_from_names():
    active = {"snapshot_id": "same"}
    mode = publication_mode
    assert mode(data_seeded=False, active_release=None, desired_snapshot_id="same") == "first"
    assert mode(data_seeded=True, active_release=None, desired_snapshot_id="same") == "first"
    assert mode(data_seeded=True, active_release=active, desired_snapshot_id="new") == "warm"
    assert mode(data_seeded=True, active_release=active, desired_snapshot_id="same") == "repeat"
    assert (
        publication_mode(
            data_seeded=True, active_release=active, desired_snapshot_id="same", config_only=True
        )
        == "config_only"
    )
    incompatible = mode(
        data_seeded=True,
        active_release=active,
        desired_snapshot_id="new",
        schema_compatible=False,
    )
    assert incompatible == "migration"
    # A project called "warm-qa" with no seeded data is still a first publish.
    journal = {"slug": "warm-qa", "data_seeded": False}
    result = profile(QA, journal, desired_snapshot_id="new", logs=None)
    assert result.mode == "first" and result.timings.total_ms is None


def test_recorded_baseline_is_consistent():
    baseline = load_baseline()
    assert re.fullmatch(r"[0-9a-f]{40}", baseline["base_sha"])
    qa = qa_project_ids(baseline)
    labels = [run["label"] for run in baseline["runs"]]
    assert len(labels) == len(set(labels))
    for run in baseline["runs"]:
        assert UUID(run["project_id"]) in qa, run["label"]
        journal = run["journal"]
        mode = publication_mode(
            data_seeded=journal["data_seeded_before"],
            active_release={"snapshot_id": journal.get("previous_snapshot_id")}
            if journal["active_release_before"]
            else None,
            desired_snapshot_id=journal["snapshot_id"],
        )
        assert mode in MODES
        for key in ("prepare", "activate", "total"):
            value = run["timings_ms"].get(key)
            assert value is None or (isinstance(value, int) and value > 0), (run["label"], key)
        for key in ("source_pause_s", "public_unavailable_s"):
            value = run.get(key)
            assert value is None or (isinstance(value, (int, float)) and value >= 0), run["label"]
        if run["timings_ms"].get("total") is not None:
            assert run["started_at"] and run["finished_at"], run["label"]
    assert set(baseline["not_measured"]) >= {"repeat-same-release"}
