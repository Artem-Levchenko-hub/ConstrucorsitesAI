"""FV032: the orchestrator extractor produces exactly the golden manifest the
API extractor is held to; both implement the same normative rules."""

from __future__ import annotations

import json
from pathlib import Path

from yleum_orchestrator.services.versioning.compatibility import (
    capability_diff,
    describe_capability,
    route_capabilities,
    route_manifest,
)

CORPUS = Path(__file__).parent / "fixtures" / "versioning_v4" / "routes" / "corpus.json"


def test_orchestrator_extractor_matches_the_golden_corpus():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    manifest = route_manifest(corpus["files"])
    assert sorted(map(list, manifest.capabilities)) == corpus["expected"]["capabilities"]
    assert sorted(manifest.unresolved) == corpus["expected"]["unresolved"]
    assert sorted(map(list, route_capabilities(corpus["files"]))) == corpus["expected"]["owned"]


def test_capability_diff_uses_ownership_and_reports_export_star():
    get = "export async function GET() {}\n"
    kit = "export function GET() {}\nexport function POST() {}\n"
    current = {
        "src/app/api/visits/route.ts": get,
        "src/app/api/legacy/route.ts": 'export * from "./impl";\n',
        "src/app/api/omnia/config/route.ts": kit,
    }
    historical = {"src/app/api/omnia/config/route.ts": kit}
    diff = capability_diff(current, historical)
    assert [(c.method, c.path) for c in diff.lost] == [("GET", "/api/visits"), ("*", "/api/legacy")]
    assert diff.restored == []
    assert describe_capability(diff.lost[1]).startswith("маршрут (export *)")
    # A kit route the historical version changed is compared, not skipped.
    older = {"src/app/api/omnia/config/route.ts": "export function GET() {}\n"}
    diff = capability_diff({"src/app/api/omnia/config/route.ts": kit}, older)
    assert [(c.method, c.path) for c in diff.lost] == [("POST", "/api/omnia/config")]
