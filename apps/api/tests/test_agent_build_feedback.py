"""Confirmed compiler failures reach the next model request with current source."""
from copy import deepcopy
from importlib import import_module, util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder, agent_native
from yleum_api.services.generation.agent_runtime import checked_fast_check
from yleum_api.services.generation.source_error_classification import SourceFailure

from .test_agent_native_truncation import turn

ENTRY = "src/app/page.tsx"
PROFILE = "src/lib/omnia/app-profile-actions.ts"
CONFIG = "src/lib/omnia/max-config.ts"
TEMPLATE = Path(__file__).resolve().parents[2] / "orchestrator/templates/max-miniapp-nextjs"
DIAGNOSTIC = (
    "src/app/page.tsx(5,20): error TS2339: Property 'name' does not exist on type "
    "'YleumAppProfile'.\n"
    "src/app/page.tsx(6,20): error TS2339: Property 'name' does not exist on type "
    "'YleumMaxContentItem'."
)
FAILURE = SourceFailure("type_error", ENTRY, (ENTRY,))


def helper():
    name = "yleum_api.services.agent_build_feedback"
    assert util.find_spec(name), "compiler feedback must be preserved beyond observation caps"
    return import_module(name)


def files():
    return {
        ENTRY: "\n".join([
            "// current owned source", "// second", "// third", "// fourth",
            "const heading = profile.name;", "const caption = item.name;",
            "export default function Page() { return <main>{heading}{caption}</main>; }",
        ]),
        PROFILE: (TEMPLATE / PROFILE).read_text(encoding="utf-8"),
        CONFIG: (TEMPLATE / CONFIG).read_text(encoding="utf-8")
        + '\nexport const privateBusinessLiteral = "DO_NOT_DISCLOSE_BUSINESS";',
    }


def assert_repair_context(value):
    assert "TS2339" in value
    assert "const heading = profile.name;" in value
    assert "const caption = item.name;" in value
    assert "export type YleumAppProfile = {" in value
    assert "app_name: string;" in value
    assert "export type YleumMaxContentItem = {" in value
    assert "title: string;" in value
    assert "omniaMaxConfig:" not in value and "DO_NOT_DISCLOSE_BUSINESS" not in value


def test_context_contains_current_locations_and_exact_sdk_declarations_only():
    context = helper().build_compiler_repair_context(DIAGNOSTIC, FAILURE, files())
    assert_repair_context(context)
    assert '"complete":false' in context  # Snippets do not claim a complete-file witness.
    assert len(context.encode("utf-8")) <= 16 * 1024


@pytest.mark.parametrize("kind", ["infrastructure", "unknown", "external_package"])
def test_non_source_failure_cannot_gain_context_from_ts_shaped_log(kind):
    assert helper().build_compiler_repair_context(
        DIAGNOSTIC, SourceFailure(kind, ENTRY, (ENTRY,)), files(),
    ) is None


@pytest.mark.parametrize("source", [
    "sk-" + "s" * 32, "x" * (64 * 1024 + 1), "\ud800",
    "export const password = process.env.DATABASE_URL;",
    'export const url = "postgresql://reader:DO_NOT_DISCLOSE@db.invalid/app";',
    'export const credentials = {"DATABASE_URL":"DO_NOT_DISCLOSE"};',
], ids=["credential", "oversized", "invalid-utf8", "sensitive-env", "dsn", "credential-map"])
def test_withheld_source_has_no_false_complete_witness_or_sensitive_material(source):
    current = files()
    current[ENTRY] = source
    context = helper().build_compiler_repair_context(DIAGNOSTIC, FAILURE, current)
    assert "withheld" in context
    assert "sk-" not in context and "process.env" not in context and "\ud800" not in context
    assert "DO_NOT_DISCLOSE" not in context
    assert '"complete":true' not in context
    assert len(context.encode("utf-8")) <= 16 * 1024


@pytest.mark.parametrize("path", ["../secret.tsx", ".env.tsx", CONFIG, "src/lib/unknown.ts"])
def test_unknown_or_protected_target_is_never_quoted(path):
    current = {path: "DO_NOT_DISCLOSE"}
    context = helper().build_compiler_repair_context(
        DIAGNOSTIC, SourceFailure("type_error", path, (path,)), current,
    )
    assert context is None or "DO_NOT_DISCLOSE" not in context


async def test_fast_check_uses_authoritative_failure_then_fresh_owned_snapshot():
    current = files()
    receipt = SimpleNamespace(id=uuid4(), outcome="red", redacted_detail=DIAGNOSTIC)
    order = []

    async def source_failure(result):
        assert result is receipt
        order.append("authoritative")
        return FAILURE

    async def snapshot():
        order.append("snapshot")
        return dict(current)

    runtime = SimpleNamespace(
        migration_baseline=None, verified_progress=None,
        coordinator=SimpleNamespace(
            fast_check=AsyncMock(return_value=receipt), source_failure=source_failure,
        ), handle=SimpleNamespace(snapshot_files=snapshot),
    )
    first = await checked_fast_check(runtime, SimpleNamespace())
    assert_repair_context(first.get("compiler_repair_context", ""))
    current[ENTRY] = current[ENTRY].replace("profile.name", "profile.app_name")
    second = await checked_fast_check(runtime, SimpleNamespace())
    assert "const heading = profile.app_name;" in second["compiler_repair_context"]
    assert "const heading = profile.name;" not in second["compiler_repair_context"]
    assert order == ["authoritative", "snapshot", "authoritative", "snapshot"]
    assert first["ok"] is False and second["ok"] is False


@pytest.mark.parametrize("failure", [None, SourceFailure(), SourceFailure("infrastructure")])
async def test_unowned_stale_unknown_or_infrastructure_receipt_never_reads_source(failure):
    receipt = SimpleNamespace(id=uuid4(), outcome="red", redacted_detail=DIAGNOSTIC)
    snapshot = AsyncMock(side_effect=AssertionError("unconfirmed receipt cannot read source"))
    runtime = SimpleNamespace(
        migration_baseline=None, verified_progress=None,
        coordinator=SimpleNamespace(
            fast_check=AsyncMock(return_value=receipt),
            source_failure=AsyncMock(return_value=failure),
        ), handle=SimpleNamespace(snapshot_files=snapshot),
    )
    assert await checked_fast_check(runtime, SimpleNamespace()) == {
        "ok": False, "detail": DIAGNOSTIC,
    }


@pytest.mark.parametrize("route", ["legacy", "native_explicit", "native_early"])
async def test_all_routes_deliver_feedback_after_caps_to_next_provider(monkeypatch, route):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    get_settings.cache_clear()
    calls, events = [], []
    current = files()
    receipt = SimpleNamespace(
        id=uuid4(), outcome="red", redacted_detail="compiler preamble\n" * 2300 + DIAGNOSTIC,
    )

    async def snapshot():
        return dict(current)

    runtime = SimpleNamespace(
        migration_baseline=None, verified_progress=None,
        handle=SimpleNamespace(snapshot_files=snapshot),
        coordinator=SimpleNamespace(
            fast_check=AsyncMock(return_value=receipt),
            source_failure=AsyncMock(return_value=FAILURE),
        ),
    )

    async def execute(action):
        if action.name == "write_file":
            current[ENTRY] = action.args["content"]
            return {"ok": True}
        assert action.name == "build"
        return await checked_fast_check(runtime, SimpleNamespace())

    async def emit(event, payload):
        events.append((event, payload))

    async def legacy_provider(messages, _model, **kwargs):
        calls.append(deepcopy(messages))
        name = "build" if len(calls) == 1 else "done"
        return f'<omnia:action name="{name}">{{"summary":"fixture"}}</omnia:action>'

    async def native_provider(*args, **kwargs):
        calls.append(deepcopy(args[2]))
        if len(calls) == 1:
            return turn(("write_file", {"path": ENTRY, "content": current[ENTRY]})) \
                if route == "native_early" else turn(("build", {}))
        return turn(("done", {"summary": "fixture"}))

    try:
        if route == "legacy":
            await agent_builder.run_agent_build(
                system_prompt="fixture", user_prompt="fixture", model="fixture",
                execute=execute, complete=legacy_provider, max_steps=2, max_segments=1,
                ship_green_on_abort=False, emit=emit,
            )
        else:
            monkeypatch.setattr(agent_native, "_call_messages", native_provider)
            await agent_native._run_native_segment(
                system="MAX VERIFICATION OVERRIDE", task="fixture", execute=execute,
                initial_files={**current, ENTRY: "old source"}, portable_cell=True,
                max_steps=2, emit=emit,
            )
        assert len(calls) >= 2
        assert_repair_context(str(calls[1]))
        assert "export type YleumAppProfile" not in str(events)
        if route == "native_early":
            assert "EARLY SOURCE CHECK:" in str(calls[1])
    finally:
        get_settings.cache_clear()


def test_renderer_keeps_protocol_body_precedence_and_redacts_feedback():
    obs = {"detail": "legacy detail", "content": "native content",
           "compiler_repair_context": "fresh context sk-" + "s" * 32}
    legacy = helper().render_model_observation(obs, max_body_chars=9)
    native = helper().render_model_observation(obs, max_body_chars=9, prefer_content=True)
    assert legacy.startswith("legacy de") and native.startswith("native co")
    assert "fresh context" in legacy and "fresh context" in native
    assert "sk-" not in legacy + native
