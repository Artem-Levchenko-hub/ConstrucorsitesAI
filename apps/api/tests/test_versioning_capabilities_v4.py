"""AV06.1: the route source-check cannot be fooled by comments/strings, follows
aliases and re-exports, normalizes groups/params, reports `export *` as a
coverage gap and treats platform kit routes by ownership. FV025–FV029, FV032."""

from __future__ import annotations

import json
from pathlib import Path

from omnia_api.services.versioning_capabilities import (
    assess_capabilities,
    blank_comments_and_strings,
    capability_gap,
    exported_methods,
    lost_capabilities,
    normalize_route,
    route_capabilities,
    route_manifest,
)

CORPUS = (
    Path(__file__).resolve().parents[2]
    / "orchestrator" / "tests" / "fixtures" / "versioning_v4" / "routes" / "corpus.json"
)
GET = "export async function GET() { return Response.json([]) }\n"
VISITS = {"src/app/api/visits/route.ts": GET}


def test_golden_corpus_matches_the_normative_manifest():  # FV032 (API side)
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    manifest = route_manifest(corpus["files"])
    assert sorted(map(list, manifest.capabilities)) == corpus["expected"]["capabilities"]
    assert sorted(manifest.unresolved) == corpus["expected"]["unresolved"]
    assert sorted(map(list, route_capabilities(corpus["files"]))) == corpus["expected"]["owned"]


def test_comments_and_strings_neither_create_nor_hide_handlers():  # FV025
    hidden = (
        "// export function GET() {}\n/* export const POST = 1 */\n"
        "const s = `export function PUT() {}`;\n"
    )
    assert exported_methods(hidden) == (frozenset(), False)
    assert "GET" not in blank_comments_and_strings(hidden)
    before, after = VISITS, {"src/app/api/visits/route.ts": hidden}
    assert lost_capabilities(before, after) == [("GET", "/api/visits")]
    assert "GET /api/visits" in (capability_gap(before, after) or "")


def test_alias_and_reexport_are_real_handlers():  # FV026
    after = {
        "src/app/api/visits/route.ts": 'import { list } from "./h";\nexport { list as GET };\n'
    }
    assert lost_capabilities(VISITS, after) == []
    after = {"src/app/api/visits/route.ts": 'export { GET } from "./handlers";\n'}
    assert lost_capabilities(VISITS, after) == []
    assert exported_methods("export { a as GET, b as POST, type Visit }")[0] == {"GET", "POST"}


def test_route_group_and_slot_do_not_change_the_url():  # FV027
    after = {"src/app/(data)/@modal/api/visits/route.ts": GET}
    assert lost_capabilities(VISITS, after) == []
    assert normalize_route("(data)/@modal/api/visits/") == "/api/visits"


def test_parameter_rename_is_the_same_endpoint():  # FV028
    before = {"src/app/api/clients/[id]/route.ts": GET}
    after = {"src/app/api/clients/[clientId]/route.ts": GET}
    assert lost_capabilities(before, after) == []


def test_catch_all_and_optional_catch_all_stay_distinct():  # FV029
    before = {"src/app/api/files/[...path]/route.ts": GET}
    after = {"src/app/api/files/[[...path]]/route.ts": GET}
    assert lost_capabilities(before, after) == [("GET", "/api/files/[...]")]


def test_export_star_is_a_coverage_gap_not_an_empty_list():
    star = 'export * from "./impl";\n'
    manifest = route_manifest({"src/app/api/legacy/route.ts": star})
    assert manifest.capabilities == frozenset() and manifest.unresolved == {"/api/legacy"}
    # In the result: not provable → the agent is asked for explicit exports.
    gap = capability_gap(VISITS, {"src/app/api/visits/route.ts": star})
    assert gap is not None and "export * from" in gap and "GET /api/visits" in gap
    # In the baseline: the route must at least survive in some form.
    baseline = {"src/app/api/legacy/route.ts": star}
    assert lost_capabilities(baseline, baseline) == []
    assert lost_capabilities(baseline, {"src/app/api/legacy/route.ts": GET}) == []
    assert lost_capabilities(baseline, {}) == [("*", "/api/legacy")]
    # `export * as ns from` exports a namespace, not handlers.
    assert exported_methods('export * as h from "./impl";\nexport function GET() {}') == (
        frozenset({"GET"}), False,
    )


def test_head_and_options_are_capabilities_too():
    text = "export function HEAD() {}\nexport const OPTIONS = () => new Response();\n"
    assert exported_methods(text)[0] == {"HEAD", "OPTIONS"}


def test_platform_routes_count_only_when_the_app_owns_the_change():
    kit = "export function GET() { return Response.json({}) }\nexport function POST() {}\n"
    before = {
        **VISITS, "src/app/api/omnia/config/route.ts": kit, "src/app/api/health/route.ts": GET,
    }
    # Untouched kit files are ignored...
    assert lost_capabilities(before, dict(before)) == []
    assert ("GET", "/api/omnia/config") not in route_capabilities(before)
    # ...a modified one is compared like any owned route...
    changed = {**before, "src/app/api/omnia/config/route.ts": "export function GET() {}\n"}
    assert lost_capabilities(before, changed) == [("POST", "/api/omnia/config")]
    # ...and a deleted one is a loss, not a platform exception.
    deleted = {path: text for path, text in before.items() if "health" not in path}
    assert lost_capabilities(before, deleted) == [("GET", "/api/health")]


def test_assessment_carries_digests_that_change_with_the_candidate():
    first = assess_capabilities(VISITS, VISITS)
    second = assess_capabilities(VISITS, {**VISITS, "src/app/page.tsx": "x"})
    assert first.baseline_digest == second.baseline_digest
    assert first.result_digest != second.result_digest
    assert first.gap() is None
    gap = assess_capabilities(VISITS, {}).gap()
    assert gap is not None and f"candidate={second.result_digest[:12]}" not in gap
    assert f"baseline={first.baseline_digest[:12]}" in gap


def test_multi_declarator_destructured_and_no_semicolon_exports_count():
    source = (
        "const h = () => new Response();\n"
        "export const GET = h, POST = h;\n"
        "export const { PUT, DELETE: remove } = handlers;\n"
        "export const [OPTIONS] = [h];\n"
        "export let PATCH = async (req: Request, { params }: { params: { id: string } }) => {\n"
        "  return new Response()\n"
        "}\n"
        "export var HEAD = h\n"
        "const other = 1\n"
    )
    methods, unresolved = exported_methods(source)
    assert methods == frozenset({"GET", "POST", "PUT", "OPTIONS", "PATCH", "HEAD"})
    assert unresolved is False  # DELETE is bound to `remove`: not a handler export
