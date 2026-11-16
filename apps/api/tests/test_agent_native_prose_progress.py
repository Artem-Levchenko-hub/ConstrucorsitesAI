"""Provider narration must share the existing progress budget with tool turns."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native


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


def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": f"tool-{name}", "name": name, "input": args}],
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
