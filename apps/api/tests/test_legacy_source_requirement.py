"""Required edits use real workspace changes, not a successful old build."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder
from yleum_api.services.generation import agent_generation
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.generation.source_intent import source_change_required


@pytest.mark.parametrize(("prompt", "expected"), [
    ("Please review the application", False),
    ("Пожалуйста, объясни устройство приложения", False),
    ("Нужен анализ производительности", False),
    ("Нужно проверить приложение без правок", False),
    ("Could you please inspect the code", False),
    ("Please don't modify any files", False),
    ("Ничего не меняй в приложении", False),
    ("Хочу read-only поле даты в форме", True),
    ("I want a read-only date field in the form", True),
    ("Сверстай экран с надписью «ничего не меняй»", True),
    ("Ничего не меняй в данных. Исправь цвет кнопки", True),
    ("Ничего не меняй в данных. Используй референс для дизайна этот", True),
    ("Используй референс для дизайна этот. Ничего не меняй в данных", True),
    ("Ничего не меняй в данных. Объясни устройство приложения", False),
    ("Ничего не меняй в данных", False),
    ("Добавь дату. Ничего не меняй", False),
    ("Используй референс для дизайна этот", True),
    ("Макет должен выглядеть как на прикреплённом фото", True),
    ("продолжи", False),
    ("Пожалуйста, продолжай!", False),
    ("Continue", False),
    ("Продолжи. Добавь дату", True),
    ("Continue reviewing the latest update.", False),
    ("Продолжи исследование текущего кода", False),
    ("This is a read-only review of the latest change.", False),
    ("Continue reviewing the code and fix the date input", True),
])
def test_source_requirement_distinguishes_request_scope_from_literals(prompt, expected):
    assert source_change_required(prompt) is expected


def action(name, **args):
    return f'<omnia:action name="{name}">{json.dumps(args)}</omnia:action>'


@pytest.fixture
def legacy_workspace(monkeypatch):
    from yleum_api.services import max_behavior_proof

    settings = get_settings().model_copy(
        update={
            "use_native_agent": False,
            "agent_max_segments": 1,
            "agent_require_green_before_done": True,
        }
    )
    monkeypatch.setattr(agent_generation, "get_settings", lambda: settings)

    async def freeze(*args, **kwargs):
        return None

    monkeypatch.setattr(max_behavior_proof, "freeze_for_turn", freeze)
    initial = {"src/app/sever.css": "before", "DESIGN.md": "notes"}
    tree = dict(initial)
    executed, messages = [], []

    async def snapshot():
        return dict(tree)

    async def execute(tool):
        executed.append(tool.name)
        if tool.name == "write_file":
            tree[tool.path] = tool.args["content"]
        elif tool.name == "bash" and tool.args.get("cmd") == "apply-real-edit":
            tree["src/app/sever.css"] = "shell edit"
        return {"ok": True, "detail": "clean", "content": tree.get(tool.path, "")}

    async def run(replies, prompt="Используй референс для дизайна этот", is_edit=False):
        scripted = iter(replies)

        async def complete(conversation, *args, **kwargs):
            messages.append(conversation)
            return next(scripted, action("done", summary="claimed done"))

        monkeypatch.setattr(agent_builder.llm_client, "complete_chat", complete)
        handle = SimpleNamespace(
            is_portable=lambda: True,
            capabilities={"portable_machine": True},
            export_files=snapshot,
            snapshot_files=snapshot,
        )
        return await agent_generation.execute_agent_turn(
            _agent_res=None,
            _is_edit=is_edit,
            _max_has_generated_snapshot=True,
            _max_seed_files={},
            _max_shell_enabled=True,
            baseline=SourceBaseline(uuid4(), "a" * 40, initial),
            ids=GenerationIds(*(uuid4() for _ in range(5))),
            is_free=False,
            project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text=prompt,
            runtime=GenerationRuntime(handle=handle, coordinator=SimpleNamespace()),
            plan=SimpleNamespace(
                system="sys",
                user=prompt,
                model="test",
                escalate_model=None,
                steps=12,
                bare_stack=False,
            ),
            operations=SimpleNamespace(
                execute=execute,
                emit=None,
                snapshot_files=snapshot,
                edit_deadline=datetime.now(UTC) + timedelta(minutes=10),
            ),
        )

    return run, initial, tree, executed, messages


async def test_exact_sever_reads_and_green_old_build_cannot_finish_required_edit(legacy_workspace):
    run, initial, tree, executed, messages = legacy_workspace
    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await run(
            [
                action("read_file", path="src/app/sever.css"),
                action("bash", cmd="ls"),
                action("bash", cmd="find . -type f"),
                action("done", summary="design changed"),
                action("build"),
                action("done", summary="design changed"),
            ]
        )
    assert executed == ["read_file", "bash", "bash"]
    assert tree == initial and len(messages) <= 12


@pytest.mark.parametrize("via_shell", [False, True])
async def test_actual_file_or_shell_edit_allows_verification(legacy_workspace, via_shell):
    run, initial, tree, executed, _ = legacy_workspace
    edit = (
        action("bash", cmd="apply-real-edit")
        if via_shell
        else action("write_file", path="src/app/sever.css", content="real edit")
    )
    result, failure = await run([edit, action("build"), action("done")])
    assert result.done and result.needs_finalization and failure is None
    assert tree != initial and executed == [("bash" if via_shell else "write_file"), "build"]


async def test_restored_changed_draft_can_build_without_another_model_write(legacy_workspace):
    run, _, tree, executed, _ = legacy_workspace
    tree["src/app/sever.css"] = "restored draft"
    result, failure = await run([action("build"), action("done")])
    assert result.done and failure is None and executed == ["build"]


@pytest.mark.parametrize("content", ["before", "metadata only"])
async def test_unchanged_or_documentation_write_does_not_unlock_build(legacy_workspace, content):
    run, _, _, executed, _ = legacy_workspace
    path = "src/app/sever.css" if content == "before" else "DESIGN.md"
    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await run(
            [action("write_file", path=path, content=content), action("build"), action("done")]
        )
    assert "build" not in executed


async def test_reverting_to_accepted_source_invalidates_green_completion(legacy_workspace):
    run, initial, tree, executed, _ = legacy_workspace
    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await run(
            [
                action("write_file", path="src/app/sever.css", content="changed"),
                action("build"),
                action("write_file", path="src/app/sever.css", content="before"),
                action("done"),
            ]
        )
    assert tree == initial and executed.count("build") == 1


async def test_explicit_readonly_request_does_not_force_a_source_change(legacy_workspace):
    run, initial, tree, executed, _ = legacy_workspace
    result, failure = await run(
        [action("read_file", path="src/app/sever.css"), action("build"), action("done")],
        prompt="Ничего не меняй",
    )
    assert result.done and failure is None and tree == initial
    assert executed == ["read_file", "build"]


@pytest.mark.parametrize("tool", ["done", "build", "runtime_check", "probe"])
async def test_repeated_premature_verification_uses_existing_no_progress_bound(tool):
    calls = 0
    executed = []

    async def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        return action(tool)

    async def execute(tool):
        executed.append(tool.name)
        return {"ok": True}

    result = await agent_builder.run_agent_build(
        system_prompt="sys", user_prompt="modify existing app", model="test",
        complete=complete, execute=execute, edit_source_changed=lambda: False,
        require_green_before_done=True, coordinator_handoff=True,
        ship_green_on_abort=True, max_steps=30,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "no_progress" and calls == 14
    assert executed == []


@pytest.mark.parametrize("revert_before_abort", [False, True])
async def test_green_abort_requires_source_to_still_differ_from_accepted_baseline(
    revert_before_abort,
):
    tree = {"src/app/page.tsx": "before"}
    calls = 0

    async def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return action("write_file", path="src/app/page.tsx", content="after")
        if calls == 2:
            return action("build")
        if revert_before_abort:
            tree["src/app/page.tsx"] = "before"
        return action("read_file", path="src/app/page.tsx")

    async def execute(tool):
        if tool.name == "write_file":
            tree[tool.path] = tool.args["content"]
        return {"ok": True, "content": tree["src/app/page.tsx"]}

    result = await agent_builder.run_agent_build(
        system_prompt="sys", user_prompt="modify existing app", model="test",
        complete=complete, execute=execute,
        edit_source_changed=lambda: tree["src/app/page.tsx"] != "before",
        require_green_before_done=True, coordinator_handoff=True,
        ship_green_on_abort=True, max_steps=3,
    )
    assert result.done is (not revert_before_abort)
    assert result.needs_finalization is (not revert_before_abort)


def test_progress_guidance_does_not_turn_an_old_green_tree_into_completion():
    note = agent_builder._progress_note(
        {"src/app/page.tsx": "claimed source"}, True, source_change_pending=True,
    )
    assert "actual application source is unchanged" in note
    assert "Apply the requested change" in note
    assert "LAST build: CLEAN" not in note
    assert "FILES YOU HAVE ALREADY WRITTEN" not in note
