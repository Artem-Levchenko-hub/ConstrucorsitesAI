"""Generation cannot schedule visual-judge calls, including stale model replies."""

from __future__ import annotations

from typing import Any

import pytest

from omnia_api.services import agent_builder, agent_native, agent_vision


@pytest.mark.parametrize(
    "template",
    ["bare-nextjs", "nextjs-postgres-drizzle", "nextjs-entities", "max-miniapp-nextjs"],
)
def test_stack_prompt_does_not_reintroduce_visual_action(template: str) -> None:
    guide = agent_builder.load_stack_system_prompt(template)
    assert guide, f"expected existing guide for {template}"
    assert "`see" not in guide
    assert "runtime_check" in agent_builder.build_system_prompt(guide)


@pytest.mark.parametrize(
    "tools",
    [
        agent_native._TOOLS_CACHED,
        agent_native._MAX_TOOLS_CACHED,
        agent_native._MAX_TOOLS_WITH_BASH_CACHED,
        agent_native._MAX_ENTRY_WRITE_TOOLS,
    ],
)
def test_generation_toolsets_keep_functional_tools_without_visual_judge(tools: list[Any]) -> None:
    names = {tool["name"] for tool in tools}
    assert "see" not in names
    if "done" in names:
        assert {"build", "runtime_check"}.issubset(names)


@pytest.mark.asyncio
async def test_container_executor_rejects_visual_action_without_calling_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_vision(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pytest.fail("removed visual tool must not start a screenshot or model request")

    monkeypatch.setattr(agent_vision, "see_page", forbidden_vision)
    execute = agent_builder.make_container_executor(project_id="test-project", slug="test-slug")
    result = await execute(agent_builder.Action("see", {"path": "/"}))
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_native_rejects_stale_visual_tool_call_and_still_finishes_functionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(
        [
            [("see", {"path": "/"})],
            [("build", {}), ("runtime_check", {"path": "/"})],
            [("done", {"summary": "functionally verified"})],
        ]
    )

    async def provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "content": [
                {"type": "tool_use", "id": f"call-{index}", "name": name, "input": args}
                for index, (name, args) in enumerate(next(replies))
            ],
            "stop_reason": "tool_use",
        }

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    actions: list[str] = []

    async def execute(action: agent_builder.Action) -> dict[str, Any]:
        actions.append(action.name)
        return {"ok": True, "detail": "verified"}

    def completion(_files: Any, evidence: Any) -> str | None:
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check after the last source write."
        return None

    result = await agent_native.run_native_build(
        system="functional generation",
        task="verify app",
        execute=execute,
        completion_check=completion,
        max_steps=5,
    )
    assert result.done is True
    assert actions == ["build", "runtime_check"]
    assert result.evidence.get("see", 0) == 0


@pytest.mark.asyncio
async def test_provider_stop_does_not_autorun_obsolete_visual_completion_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "finished"}], "stop_reason": "end_turn"}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    actions: list[str] = []

    async def execute(action: agent_builder.Action) -> dict[str, Any]:
        actions.append(action.name)
        return {"ok": True, "detail": "build green"}

    result = await agent_native.run_native_build(
        system="functional generation",
        task="verify app",
        execute=execute,
        completion_check=lambda _files, _evidence: "Run see once from old checkpoint.",
        max_steps=1,
    )
    assert result.done is False
    assert actions == ["build"]
