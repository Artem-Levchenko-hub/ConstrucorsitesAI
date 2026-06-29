"""Unit tests for the cross-tenant isolation gate (pure cores + wiring).

The browser-driven halves (run_public_access_gate / run_isolation_probe) need a
live container, so here we test the PURE logic they delegate to — route parsing,
the leak judgement, id extraction — plus the agent-action wiring and the
stack-aware remediation rule. The risky decisions are all in these pure pieces.
"""

from __future__ import annotations

from omnia_api.services.isolation_gate import (
    api_routes_from_grep,
    body_contains_id,
    body_leaks_data,
    extract_resource_id,
)

# ── api_routes_from_grep ─────────────────────────────────────────────────────


def test_routes_parse_static_collection() -> None:
    dump = (
        "src/app/api/tasks/route.ts:3:export async function GET() {}\n"
        "src/app/api/tasks/route.ts:9:export async function POST() {}\n"
        "src/app/api/projects/route.ts:1:export const GET = ...\n"
    )
    assert api_routes_from_grep(dump) == ["/api/projects", "/api/tasks"]


def test_routes_drop_dynamic_auth_and_groups() -> None:
    dump = (
        "src/app/api/tasks/[id]/route.ts:2:export async function GET() {}\n"  # dynamic
        "src/app/api/auth/register/route.ts:2:export async function POST() {}\n"  # auth
        "src/app/api/(admin)/users/route.ts:2:export async function GET() {}\n"  # group
        "src/app/api/notes/route.ts:2:export async function GET() {}\n"  # keep
    )
    routes = api_routes_from_grep(dump)
    assert "/api/notes" in routes
    assert "/api/users" in routes  # the (admin) group segment is stripped from the URL
    assert all("[" not in r for r in routes)
    assert all(not r.startswith("/api/auth") for r in routes)


def test_routes_dedupe_and_cap() -> None:
    lines = "".join(
        f"src/app/api/r{i}/route.ts:1:export async function GET() {{}}\n" for i in range(30)
    )
    routes = api_routes_from_grep(lines)
    assert len(routes) == 12  # _MAX_ROUTES
    assert len(set(routes)) == len(routes)


def test_routes_empty_and_garbage() -> None:
    assert api_routes_from_grep("") == []
    assert api_routes_from_grep("(no matches)") == []
    assert api_routes_from_grep("src/app/api/route.ts:1:export GET") == []  # no resource seg


# ── body_leaks_data (the leak judgement) ─────────────────────────────────────


def test_leak_nonempty_list_to_anon_is_a_leak() -> None:
    assert body_leaks_data(200, [{"id": 1}]) is True


def test_leak_data_envelope_with_rows() -> None:
    assert body_leaks_data(200, {"data": [{"id": 1}]}) is True
    assert body_leaks_data(200, {"items": [{"x": 1}]}) is True


def test_no_leak_on_denied_or_redirect_or_error() -> None:
    for status in (401, 403, 302, 404, 500):
        assert body_leaks_data(status, [{"id": 1}]) is False


def test_no_leak_on_empty_or_bare_object() -> None:
    assert body_leaks_data(200, []) is False  # empty list = nothing leaked
    assert body_leaks_data(200, {}) is False
    assert body_leaks_data(200, {"data": []}) is False
    assert body_leaks_data(200, {"ok": True}) is False  # bare status object


# ── id helpers ───────────────────────────────────────────────────────────────


def test_extract_id_top_level_and_nested() -> None:
    assert extract_resource_id({"id": 42}) == "42"
    assert extract_resource_id({"data": {"id": "abc"}}) == "abc"
    assert extract_resource_id({"nothing": 1}) is None
    assert extract_resource_id([1, 2]) is None


def test_body_contains_id() -> None:
    assert body_contains_id({"data": [{"id": "u1-row"}]}, "u1-row") is True
    assert body_contains_id({"data": []}, "u1-row") is False
    assert body_contains_id(None, "x") is False  # null body never contains a real id
    assert body_contains_id({"id": "x"}, "") is False  # empty id → never "found"


# ── verdict integration (reuses functional_gate.summarize) ───────────────────


def test_summarize_passes_only_when_all_ok() -> None:
    from omnia_api.services.functional_gate import Check, summarize

    assert summarize([Check("anon DENIED /api/tasks", True, "HTTP 401")]).passed is True
    leak = summarize(
        [
            Check("anon DENIED /api/tasks", False, "HTTP 200 — LEAKS"),
            Check("anon DENIED /api/notes", True, "HTTP 401"),
        ]
    )
    assert leak.passed is False
    assert "anon DENIED /api/tasks" in leak.summary


# ── agent action wiring ──────────────────────────────────────────────────────


def test_verify_isolation_action_registered() -> None:
    from omnia_api.services.agent_builder import _KNOWN_ACTIONS, _VERIFY_ACTIONS, parse_action

    assert "verify_isolation" in _KNOWN_ACTIONS
    assert "verify_isolation" in _VERIFY_ACTIONS  # idempotent re-check, not a cycle
    a = parse_action(
        '<omnia:action name="verify_isolation">'
        '{"create":{"method":"POST","path":"/api/tasks","body":{"t":1}},'
        '"read":{"path":"/api/tasks/{id}"}}</omnia:action>'
    )
    assert a is not None and a.name == "verify_isolation"
    assert a.args["create"]["path"] == "/api/tasks"
    assert a.args["read"]["path"] == "/api/tasks/{id}"


# ── stack-aware remediation rule ─────────────────────────────────────────────


def test_drizzle_fix_instruction_uses_isolation_rule() -> None:
    from omnia_api.services.agent_gate_feedback import GateOutcome, build_fix_instruction

    red = [GateOutcome("isolation", False, ["anon DENIED /api/tasks: HTTP 200 — LEAKS ROWS"])]
    drizzle = build_fix_instruction(red, attempt=0, max_attempts=2, stack="nextjs-postgres-drizzle")
    assert drizzle is not None
    assert "изоляции данных" in drizzle
    assert "@/lib/sdk" not in drizzle  # the entities rule must NOT be sent to drizzle

    # Default (entities/realtime) keeps the original SDK rule, byte-for-byte path.
    default = build_fix_instruction(red, attempt=0, max_attempts=2)
    assert "@/lib/sdk" in default
