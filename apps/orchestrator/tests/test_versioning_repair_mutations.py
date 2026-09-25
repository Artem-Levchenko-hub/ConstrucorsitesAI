"""AV06.3 (source half): every deterministic source mutation of an adapted
candidate is detected by the independent route check; the catalog is honest
about what still waits for behavioral/browser probes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yleum_orchestrator.services.versioning.compatibility import capability_diff

FIXTURES = Path(__file__).parent / "fixtures" / "versioning_v4"
CATALOG = json.loads((FIXTURES / "mutations.json").read_text(encoding="utf-8"))
CORPUS = json.loads((FIXTURES / "routes" / "corpus.json").read_text(encoding="utf-8"))
VISITS = "src/app/api/visits/route.ts"


def mutate(files: dict[str, str], name: str) -> dict[str, str]:
    result = dict(files)
    if name == "remove_get":
        result[VISITS] = (
            'import { create } from "./handlers";\nexport { create as POST };\n'
            'export { HEAD } from "./head";\n'
        )
    elif name == "comment_only_get":
        result[VISITS] = (
            'import { create } from "./handlers";\n// export { list as GET };\n'
            'export { create as POST };\nexport { HEAD } from "./head";\n'
            "const hint = `export function GET() {}`;\n"
        )
    elif name == "reexport_alias":
        result[VISITS] = (
            'import { list, create } from "./handlers";\nexport { list as GET, create as POST };\n'
            'export { HEAD } from "./head";\n'
        )
    elif name == "export_star":
        result[VISITS] = 'export * from "./handlers";\n'
    elif name == "platform_route_deleted":
        del result["src/app/api/omnia/config/route.ts"]
    else:
        raise KeyError(name)
    return result


SOURCE_MUTATIONS = [m for m in CATALOG["mutations"] if m["kind"] == "source"]


@pytest.mark.parametrize(
    "mutation", [m["name"] for m in SOURCE_MUTATIONS if m["name"] != "baseline_missing"]
)
def test_source_mutations_are_detected_or_proven_harmless(mutation):
    before = CORPUS["files"]
    after = mutate(before, mutation)
    lost = [(c.method, c.path) for c in capability_diff(after, before).lost]
    restored = [(c.method, c.path) for c in capability_diff(after, before).restored]
    if mutation == "remove_get":
        assert restored == [("GET", "/api/visits")] and lost == []
    elif mutation == "comment_only_get":
        assert restored == [("GET", "/api/visits")]  # the comment does not count
    elif mutation == "reexport_alias":
        assert restored == [] and lost == []
    elif mutation == "export_star":
        # The star hides the handlers: the historical version "restores" them,
        # and the unresolved route is never reported as "no routes".
        assert ("GET", "/api/visits") in restored and lost == []
        from yleum_orchestrator.services.versioning.compatibility import route_manifest

        assert "/api/visits" in route_manifest(after).unresolved
    elif mutation == "platform_route_deleted":
        assert restored == [("GET", "/api/omnia/config")]


def test_catalog_is_honest_about_what_is_not_run():
    for mutation in CATALOG["mutations"]:
        assert mutation["status"] in {"deterministic", "not_run"}
        if mutation["status"] == "not_run":
            assert mutation.get("blocked_by"), mutation["name"]
        else:
            assert mutation.get("tests"), mutation["name"]
            for test in mutation["tests"]:
                assert (Path(__file__).resolve().parents[3] / test).exists(), test
    names = [m["name"] for m in CATALOG["mutations"]]
    assert len(names) == len(set(names))
    assert {"empty_array_get", "owner_filter_removed", "button_removed"} <= {
        m["name"] for m in CATALOG["mutations"] if m["status"] == "not_run"
    }
