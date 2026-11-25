"""Provider narration must share the existing progress budget with tool turns."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native
from yleum_api.services.agent_output_budget import SOL_MODEL


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://qa:qa@127.0.0.1:59983/qa_disposable")
    monkeypatch.setenv("JWT_SECRET", "qa-native-progress-key-at-least-32-bytes")
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def narration() -> dict[str, Any]:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "I will build it."}]}


def tool(name: str, args: dict[str, Any], *, call_id: str | None = None) -> dict[str, Any]:
    return {
        "stop_reason": "tool_use",
        "content": [{
            "type": "tool_use", "id": call_id or f"tool-{name}", "name": name, "input": args,
        }],
    }


@pytest.mark.parametrize("max_project", [False, True])
@pytest.mark.asyncio
async def test_narration_without_actions_cannot_consume_the_whole_segment(
    monkeypatch: pytest.MonkeyPatch, max_project: bool,
) -> None:
    calls: list[dict[str, Any]] = []
    actions: list[str] = []

    async def provider(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return narration()

    async def execute(action: Any) -> dict[str, Any]:
        actions.append(action.name)
        return {"ok": False}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE" if max_project else "Build an application",
        task="Create the requested product",
        execute=execute,
        completion_check=lambda _files, _proofs: "The requested product is missing",
        max_steps=40,
    )

    assert len(calls) == agent_native._NO_WRITE_ABORT_AT
    assert result.stop_reason == "exploring"
    assert not result.done
    assert not result.needs_finalization
    assert not result.files
    assert not actions
    if max_project:
        assert calls[agent_native._MAX_PREWRITE_DISCOVERY_TURNS]["tool_choice"] == (
            agent_native._MAX_ENTRY_WRITE_CHOICE
        )


@pytest.mark.asyncio
async def test_product_progress_resets_narration_budget_and_green_completion_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    actions: list[str] = []

    async def provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls in {6, 12}:
            return tool("write_file", {"path": f"part-{calls}.ts", "content": "export const n=1;"})
        if calls == 13:
            return tool("build", {})
        return narration()

    async def execute(action: Any) -> dict[str, Any]:
        actions.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="Build an application", task="Build two product parts",
        execute=execute, max_steps=40,
    )

    assert calls == 14
    assert calls > agent_native._NO_WRITE_ABORT_AT
    assert result.done
    assert result.stop_reason == "done_green"
    assert set(result.files) == {"part-6.ts", "part-12.ts"}
    assert actions == ["write_file", "write_file", "build"]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_repair", [True, False])
async def test_sol_continues_reported_work_in_the_same_source_tree(
    monkeypatch: pytest.MonkeyPatch, finish_repair: bool,
) -> None:
    entry = "src/app/page.tsx"
    handler = "src/app/api/questionnaire/route.ts"
    missing = "Сохранить анкету и восстановить ответы после обновления страницы."
    workspace = {}
    observations = []

    def done(remaining: str, call_id: str) -> dict[str, Any]:
        return tool("done", {"summary": "Анкета нутрициолога", "remaining_work": remaining},
                    call_id=call_id)

    turns = [
        tool("write_file", {"path": entry, "content": "questionnaire interface"}),
        done(missing, "report-partial"),
        tool("write_file", {"path": "README.md", "content": "Documentation"},
             call_id="write-documentation"),
        done("", "close-unchanged"),
    ]
    if finish_repair:
        turns += [
            tool("write_file", {"path": handler, "content": "save and reload"},
                 call_id="write-questionnaire-handler"),
            tool("build", {}),
            done("", "close-repaired"),
        ]
    responses = iter(turns)

    async def provider(_client, _url, conversation, _system, **kwargs):
        schema = next(row["input_schema"] for row in kwargs["tools"] if row["name"] == "done")
        assert "remaining_work" in schema["required"]
        for row in conversation[-1]["content"]:
            if isinstance(row, dict) and row.get("type") == "tool_result":
                observations.append(row)
        return next(responses)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "write_file":
            workspace[action.path] = action.args["content"]
        return {"ok": True}

    async def snapshot() -> dict[str, str]:
        return workspace.copy()

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        model=SOL_MODEL, system="MAX VERIFICATION OVERRIDE", task="Сделай нутрициолога",
        execute=execute, snapshot_files=snapshot, max_steps=len(turns),
        portable_cell=True, coordinator_handoff=True,
    )

    assert result.files[entry] == "questionnaire interface"
    reported, = [r for r in observations if r["tool_use_id"] == "report-partial"]
    assert reported["is_error"] and missing in reported["content"]
    assert "current workspace" in reported["content"]
    assert result.done is finish_repair
    assert not result.needs_finalization
    if finish_repair:
        refused, = [r for r in observations if r["tool_use_id"] == "close-unchanged"]
        assert refused["is_error"] and missing in refused["content"]
        assert workspace[handler] == "save and reload"
        assert result.files[handler] == "save and reload"
    else:
        assert result.stop_reason == "product_work_remaining"
        assert missing in result.summary


@pytest.mark.asyncio
async def test_sol_keeps_reported_work_across_a_native_segment_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = "src/app/page.tsx"
    missing = "Connect questionnaire persistence."
    responses = iter([
        tool("write_file", {"path": entry, "content": "interface"}),
        tool("done", {"summary": "Draft", "remaining_work": missing}, call_id="partial"),
        tool("done", {"summary": "Ready", "remaining_work": ""}, call_id="unchanged"),
        tool("write_file", {"path": entry, "content": "interface and persistence"}),
        tool("build", {}),
        tool("done", {"summary": "Ready", "remaining_work": ""}, call_id="complete"),
    ])
    workspace = {}
    conversations = []

    async def provider(_client, _url, conversation, _system, **_kwargs):
        conversations.append(str(conversation))
        return next(responses)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "write_file":
            workspace[action.path] = action.args["content"]
        return {"ok": True}

    async def snapshot() -> dict[str, str]:
        return workspace.copy()

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        model=SOL_MODEL, system="MAX VERIFICATION OVERRIDE", task="Build a nutrition journal",
        execute=execute, snapshot_files=snapshot, max_steps=2, max_segments=3,
        portable_cell=True, coordinator_handoff=True,
    )

    assert missing in conversations[2]
    assert "Implement the reported work in source" in conversations[3]
    assert result.done and result.segments == 3
    assert result.files == {entry: "interface and persistence"}
