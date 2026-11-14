"""Exercise MAX recovery turns without using a provider or a project cell."""
import json
from copy import deepcopy

import httpx
import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native

from .test_agent_native_truncation import turn

_PAGE = "src/app/page.tsx"
_HELPER = "src/lib/products.ts"


@pytest.fixture(autouse=True)
def legacy_proof(monkeypatch):
    settings = get_settings().model_copy(update={"use_max_finalization_coordinator": False})
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)


@pytest.mark.parametrize("alternating", [False, True])
async def test_repeated_inspection_switches_to_one_small_change(monkeypatch, alternating):
    files = {_PAGE: "export const title = 'Old';", _HELPER: "export const product = 1;"}
    reads = [(_PAGE, 1), (_HELPER if alternating else _PAGE, 1)]
    expected_reads = (reads * 2) if alternating else reads
    requests, executed = [], []

    async def provider(_client, _url, convo, _system, **kwargs):
        requests.append(deepcopy(convo))
        call = len(requests)
        if call <= len(expected_reads):
            path, start = expected_reads[call - 1]
            return turn(("read_file", {"path": path, "start_line": start}))
        if call == len(expected_reads) + 1:
            # The old discovery history must not lead another broad request.
            assert len(convo) == 1 and convo[0]["role"] == "user"
            assert "Old" in str(convo[0]["content"])
            assert "read_file" not in {tool["name"] for tool in kwargs["tools"]}
            return turn(("edit_file", {"path": _PAGE, "search": "Old", "replace": "New"}))
        if call == len(expected_reads) + 2:
            return turn(("build", {}))
        return turn(("done", {}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "read_file":
            return {"ok": True, "content": files[action.path]}
        if action.name == "edit_file":
            files[action.path] = files[action.path].replace(
                action.args["search"], action.args["replace"], 1,
            )
            return {"ok": True, "content": files[action.path]}
        return {"ok": True}

    async def snapshot():
        return dict(files)

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Rename Old to New",
        execute=execute, initial_files=files, snapshot_files=snapshot,
        completion_check=lambda *_: None if "New" in files[_PAGE] else "Rename requested title",
        max_steps=10, portable_cell=True,
    )
    assert result.done
    assert executed == ["read_file"] * len(expected_reads) + ["edit_file", "build"]
    assert files[_PAGE] == "export const title = 'New';"


async def test_output_limit_resets_context_and_narrows_next_product_milestone(monkeypatch):
    files = {_HELPER: "export const product = 1;"}
    calls, executed = [], []

    async def provider(_client, _url, convo, _system, **kwargs):
        calls.append(deepcopy(convo))
        if len(calls) == 1:
            return turn(("read_file", {"path": _HELPER}))
        if len(calls) == 2:
            return turn(("write_file", {"path": _PAGE, "content": "NEVER_APPLY"}),
                        stop="max_tokens")
        if len(calls) == 3:
            assert len(convo) == 1 and convo[0]["role"] == "user"
            assert "NEVER_APPLY" not in str(convo)
            assert [tool["name"] for tool in kwargs["tools"]] == ["write_file"]
            return turn(("write_file", {"path": _PAGE, "content": "working screen"}))
        if len(calls) == 4:
            return turn(("write_file", {"path": "src/app/detail/page.tsx", "content": "details"}))
        if len(calls) == 5:
            return turn(("build", {}))
        return turn(("done", {}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "read_file":
            return {"ok": True, "content": files[action.path]}
        if action.name == "write_file":
            files[action.path] = action.args["content"]
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Build the catalog and product details",
        execute=execute, initial_files=files, max_steps=8,
        completion_check=lambda *_: None if "src/app/detail/page.tsx" in files else "Add details",
    )
    assert result.done
    assert executed == ["read_file", "write_file", "write_file", "build"]
    assert files[_PAGE] == "working screen"
    assert "NEVER_APPLY" not in str(result.transcript)


@pytest.mark.parametrize("trusted", [False, True])
async def test_gateway_transcript_rejection_allows_one_fresh_request(monkeypatch, trusted):
    requests = []
    files = {_PAGE: "original page"}
    effects = []

    def gateway(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json=turn(("write_file", {
                "path": _PAGE, "content": "accepted edit",
            })))
        if len(requests) == 3:
            assert len(payload["messages"]) == 1
            assert payload["messages"][0]["role"] == "user"
            assert "accepted edit" in str(payload["messages"])
        error = {"type": "invalid_request_error", "message": "Invalid native provider request"}
        if trusted:
            error["details"] = {
                "kind": "native_transcript_rejected", "reason": "invalid_message_content",
                "provider_charge_ambiguous": False,
            }
        return httpx.Response(400, json={"type": "error", "error": error})

    async def execute(action):
        effects.append(action.name)
        files[action.path] = action.args["content"]
        return {"ok": True}

    client = httpx.AsyncClient
    monkeypatch.setattr(agent_native.httpx, "AsyncClient", lambda: client(
        transport=httpx.MockTransport(gateway),
    ))
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Change the existing page",
        execute=execute, initial_files=files, max_steps=8,
        messages_url="https://gateway.test/v1/messages",
    )
    assert len(requests) == (3 if trusted else 2)
    assert effects == ["write_file"]
    assert files[_PAGE] == "accepted edit"
    assert result.stop_reason == "provider_error"


async def test_max_discards_reused_tool_id_before_applying_new_batch(monkeypatch):
    original = turn(("write_file", {"path": _PAGE, "content": "accepted page"}))
    duplicate = turn(("write_file", {"path": _PAGE, "content": "NEVER_APPLY"}))
    duplicate["content"][0]["id"] = original["content"][0]["id"]
    turns = iter([original, duplicate, turn(("build", {})), turn(("done", {}))])
    observed = []

    async def provider(_client, _url, convo, _system, **kwargs):
        observed.extend(str(convo))
        return next(turns)

    from unittest.mock import AsyncMock

    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Create a page", execute=execute, max_steps=6,
    )
    assert result.done and result.files == {_PAGE: "accepted page"}
    assert [call.args[0].name for call in execute.await_args_list] == ["write_file", "build"]
    assert "NEVER_APPLY" not in "".join(observed)


async def test_source_change_after_repeated_reads_reopens_discovery(monkeypatch):
    files = {_PAGE: "revision A", _HELPER: "product details"}
    read_contents, observed_toolsets = [], []
    calls = 0

    async def provider(_client, _url, _convo, _system, **kwargs):
        nonlocal calls
        calls += 1
        observed_toolsets.append({tool["name"] for tool in kwargs["tools"]})
        if calls <= 3:
            return turn(("read_file", {"path": _PAGE}))
        return turn(("build", {}), ("done", {}))

    async def execute(action):
        if action.name == "read_file":
            read_contents.append(files[action.path])
            return {"ok": True, "content": files[action.path]}
        return {"ok": True}

    snapshot_calls = 0

    async def snapshot():
        nonlocal snapshot_calls
        snapshot_calls += 1
        # Workspace bytes changed after the repeated observation was recorded.
        if snapshot_calls == 3:
            files[_PAGE] = "revision B"
        return dict(files)

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Inspect the current version",
        execute=execute, initial_files=files, snapshot_files=snapshot, max_steps=8,
    )
    assert result.done
    assert read_contents == ["revision A", "revision A", "revision B"]
    assert "read_file" in observed_toolsets[2]


@pytest.mark.parametrize("old_frame", [False, True])
async def test_recovery_restores_exact_paid_turn(monkeypatch, old_frame):
    files = {_PAGE: "original", _HELPER: "details"}
    executed, requests = [], []
    paid_frame = None
    resumed = False
    paid_response = turn(
        ("write_file", {"path": _PAGE, "content": "original" if old_frame else "NEVER_APPLY"}),
        ("write_file", {"path": _HELPER, "content": "NEVER_APPLY"}),
    )

    class Restart(BaseException):
        pass

    class Continuation:
        def restore_frame(self, kind):
            assert kind == "native"
            return deepcopy(paid_frame["value"]) if resumed else None

        async def provider(self, frame, invoke):
            nonlocal paid_frame
            if frame["value"]["step"] == 2:
                if not resumed:
                    paid_frame = deepcopy(frame)
                    if old_frame:
                        for key in (
                            "inspection_history", "inspection_focus", "inspection_source",
                            "recovery_mode", "source_diagnostic", "compiler_signature",
                            "recovery_output_cap",
                        ):
                            paid_frame["value"].pop(key, None)
                        paid_frame["value"]["request"]["tools"] = agent_native._MAX_TOOLS_CACHED
                    await invoke()
                    raise Restart
                assert frame == paid_frame
                return deepcopy(paid_response)
            return await invoke()

    async def provider(_client, _url, convo, _system, **kwargs):
        requests.append(deepcopy(convo))
        if len(requests) < 3:
            return turn(("read_file", {"path": _PAGE}))
        if len(requests) == 3:
            return paid_response
        if len(requests) == 4:
            return turn(("edit_file", {"path": _PAGE, "search": "original", "replace": "updated"}))
        return turn(("build", {}), ("done", {}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "read_file":
            return {"ok": True, "content": files[action.path]}
        if action.name == "edit_file":
            files[action.path] = action.args["replace"]
            return {"ok": True, "content": files[action.path]}
        if action.name == "write_file":
            files[action.path] = action.args["content"]
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    options = dict(
        system="MAX VERIFICATION OVERRIDE", task="Update the title", execute=execute,
        initial_files=dict(files), max_steps=8, completion_check=lambda *_: None,
        continuation=Continuation(),
    )
    with pytest.raises(Restart):
        await agent_native._run_native_segment(**options)
    resumed = True
    result = await agent_native._run_native_segment(**options)
    assert result.done
    if old_frame:
        assert executed == ["read_file", "read_file", "write_file", "write_file", "edit_file",
                            "build"]
        assert files[_HELPER] == "NEVER_APPLY"
        return
    assert executed == ["read_file", "read_file", "edit_file", "build"]
    assert len(requests) == 5
    assert len(requests[2]) == 1
    assert files == {_PAGE: "updated", _HELPER: "details"}


async def test_exhausted_settled_invalid_tool_batches_are_distinct_from_transport(monkeypatch):
    settings = get_settings().model_copy(update={"use_max_finalization_coordinator": True})
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    calls, effects = [], []

    async def provider(*args, **kwargs):
        calls.append(args[2])
        return turn(("write_file", {"path": _PAGE, "content": "NEVER_APPLY"}),
                    ("edit_file", {"path": _HELPER, "search": "incomplete"}))

    async def execute(action):
        effects.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Change source", execute=execute,
        initial_files={_PAGE: "accepted source"}, max_steps=8, portable_cell=True,
    )
    assert result.stop_reason == "incomplete_response"
    assert not result.done and not result.needs_finalization
    assert len(calls) == 3
    assert not effects


@pytest.mark.parametrize("scope, cap, expected", [
    ("aitunnel", 24_000, 24_000), ("llmgw", 24_000, None),
    ("aitunnel", True, None), ("aitunnel", 2_000_001, None),
])
async def test_output_limit_uses_only_trusted_provider_cap(monkeypatch, scope, cap, expected):
    files = {_PAGE: "original page"}
    choices = []
    partial = turn(("write_file", {"path": _PAGE, "content": "NEVER_APPLY"}), stop="max_tokens")
    partial["metadata"] = {"provider_scope": scope, "provider_output_cap_tokens": cap}

    async def provider(*args, **kwargs):
        choices.append(kwargs.get("max_tokens"))
        if len(choices) == 1:
            return partial
        if len(choices) == 2:
            return turn(("edit_file", {"path": _PAGE, "search": "original", "replace": "updated"}))
        return turn(("build", {}), ("done", {}))

    async def execute(action):
        if action.name == "edit_file":
            files[action.path] = "updated page"
            return {"ok": True, "content": files[action.path]}
        assert action.name == "build"
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        model="claude-sonnet-5.5", system="MAX VERIFICATION OVERRIDE", task="Update the page",
        execute=execute, initial_files=files, max_steps=6,
    )
    assert result.done and files[_PAGE] == "updated page"
    assert choices == [None, expected, None]


async def test_same_compiler_failure_after_different_writes_changes_the_next_task(monkeypatch):
    files = {_PAGE: "page", _HELPER: "bad"}
    requests, builds = [], []

    async def provider(_client, _url, convo, _system, **kwargs):
        requests.append((deepcopy(convo), kwargs["tools"]))
        if len(requests) <= 2:
            return turn(("write_file", {"path": _HELPER, "content": f"still bad {len(requests)}"}),
                        ("build", {}))
        if len(requests) == 3:
            return turn(("edit_file", {
                "path": _HELPER, "search": "still bad 2", "replace": "fixed",
            }))
        return turn(("build", {}), ("done", {}))

    async def execute(action):
        if action.name == "write_file":
            files[action.path] = action.args["content"]
            return {"ok": True}
        if action.name == "edit_file":
            files[action.path] = action.args["replace"]
            return {"ok": True, "content": files[action.path]}
        builds.append(files[_HELPER])
        return {"ok": files[_HELPER] == "fixed", "detail": (
            f"{_HELPER}({len(builds) + 1},3): error TS2322: Type X is not assignable to type Y."
        )}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task="Fix type error", execute=execute,
        initial_files=files, max_steps=8,
    )
    assert result.done and builds == ["still bad 1", "still bad 2", "fixed"]
    assert len(requests[2][0]) == 1
    assert "TS2322" in str(requests[2][0][0]["content"])
    assert "read_file" not in {tool["name"] for tool in requests[2][1]}
