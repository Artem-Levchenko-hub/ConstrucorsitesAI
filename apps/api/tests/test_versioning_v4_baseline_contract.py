"""AV00.R: the evidence index is complete and honest — every guarantee has a
source that exists, statuses come from a fixed vocabulary, nothing claims a
finished AV06 while behavioral/browser/live-repair cases are not run."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INDEX = REPO / "docs" / "operations" / "versioning-v4-evidence-index.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA = re.compile(r"^[0-9a-f]{7,40}$")
STATUSES = {"verified", "partial", "not_run"}
CASE_STATUSES = {"passed", "partial", "not_run", "failed", "not_applicable"}


def _index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _source_exists(source: str) -> bool:
    if source.startswith("otchet:"):
        data = json.loads((REPO / "otchet" / "data.json").read_text(encoding="utf-8"))
        return any(h["id"] == source.split(":", 1)[1] for h in data["hypotheses"])
    return (REPO / source).exists()


def test_baseline_and_components_are_pinned():
    index = _index()
    assert SHA.match(index["baseline_sha"])
    assert index["schema_version"] == 1
    for name, component in index["components"].items():
        assert SHA.match(component["source_sha"]) or component["source_sha"] == "unavailable", name
        image = component["image"]
        assert image == "unavailable" or re.match(r"^sha256:[0-9a-f]{64}$", image), name
    for fix in index["preserved_fixes"]:
        assert SHORT_SHA.match(fix["sha"]) and fix["what"]


def test_every_guarantee_has_a_real_source_and_a_known_status():
    index = _index()
    ids = [g["id"] for g in index["guarantees"]]
    assert len(ids) == len(set(ids))
    for guarantee in index["guarantees"]:
        assert guarantee["status"] in STATUSES, guarantee["id"]
        assert guarantee["sources"], guarantee["id"]
        for source in guarantee["sources"]:
            assert _source_exists(source), (guarantee["id"], source)
        if guarantee["status"] == "not_run":
            assert guarantee.get("blocked_by"), guarantee["id"]


def test_av06_is_not_declared_complete_while_behavior_cases_are_not_run():
    index = _index()
    cases = {case["id"]: case for case in index["fv_cases"]}
    behavior = [cases.get(f"FV0{n}") for n in (33, 34, 35, 36)]
    assert all(case is not None for case in behavior)
    if any(case["status"] != "passed" for case in behavior):
        assert index["av06_complete"] is False
    by_id = {g["id"]: g for g in index["guarantees"]}
    assert by_id["AV06.2.behavior"]["status"] == "not_run"
    assert by_id["AV06.3.repair"]["status"] != "verified"


def test_fv_cases_are_evidence_backed():
    index = _index()
    ids = [case["id"] for case in index["fv_cases"]]
    assert len(ids) == len(set(ids))
    for case in index["fv_cases"]:
        assert re.match(r"^FV\d{3}$", case["id"])
        assert case["status"] in CASE_STATUSES, case["id"]
        if case["status"] in {"passed", "partial"}:
            assert (REPO / case["evidence"]).exists(), case["id"]
        if case["status"] == "not_run":
            assert case.get("blocked_by"), case["id"]
        if case["status"] == "not_applicable":
            assert case.get("reason"), case["id"]
