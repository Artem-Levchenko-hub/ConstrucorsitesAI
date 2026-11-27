"""Sol hands finished source to the existing checked MAX executor without extra provider turns."""

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services import agent_builder as ab
from yleum_api.services.agent_output_budget import SOL_MODEL
from yleum_api.services.generation.recovery_execution import GenerationYielded

from .test_agent_builder import _ok_executor, _repair_action

SCREEN = "src/app/page.tsx"
ROUTE = "src/app/api/plan/route.ts"
DONE = _repair_action("done", summary="Menu ready.", remaining_work="")


async def scripted(
    tree, actions, *, check=None, continuation=None, completion=None, before_reply=None,
):
    messages, operations = [], []
    write = _ok_executor([], tree)

    async def complete(request, *_args, **_kwargs):
        messages.append(deepcopy(request))
        if before_reply:
            before_reply(len(messages), tree)
        return actions[len(messages) - 1]

    async def execute(action):
        operations.append((action.name, action.path))
        if action.name == "build" and check:
            return check(tree)
        return await write(action)

    async def snapshot():
        return dict(tree)

    result = await ab.run_agent_build(
        model=SOL_MODEL,
        system_prompt="sys",
        user_prompt="Create an app.",
        execute=execute,
        complete=complete,
        snapshot_files=snapshot,
        coordinator_handoff=True,
        continuation=continuation,
        completion_check=completion or (lambda *_: None),
        max_steps=len(actions),
    )
    return result, messages, operations


async def test_sol_applies_eleventh_distinct_write_and_checks_done_without_provider_round_trip():
    tree = {}
    paths = [f"src/components/View{index}.tsx" for index in range(11)]
    actions = [
        _repair_action("write_file", path=path, content=f"export const id = {i}")
        for i, path in enumerate(paths)
    ]
    result, messages, operations = await scripted(tree, [*actions, DONE])
    assert result.done and result.needs_finalization
    assert len(messages) == len(paths) + 1
    assert operations == [*(("write_file", path) for path in paths), ("build", "")]
    assert set(paths) == set(tree)


async def test_red_done_check_returns_real_diagnostic_and_new_source_requires_fresh_check():
    tree = {}
    diagnostic = "Type error at src/app/page.tsx: response is missing plan."
    result, messages, operations = await scripted(
        tree,
        [
            _repair_action("write_file", path=SCREEN, content="broken"),
            DONE,
            _repair_action("edit_file", path=SCREEN, search="broken", replace="fixed"),
            DONE,
        ],
        check=lambda files: {"ok": files[SCREEN] == "fixed", "detail": diagnostic},
    )
    assert result.done and result.needs_finalization and len(messages) == 4
    assert diagnostic in str(messages[2])
    assert operations == [
        ("write_file", SCREEN),
        ("build", ""),
        ("edit_file", SCREEN),
        ("build", ""),
    ]


async def test_negative_done_check_never_promotes_unfixed_source():
    result, messages, operations = await scripted(
        {},
        [
            _repair_action("write_file", path=SCREEN, content="broken"),
            DONE,
            DONE,
        ],
        check=lambda _: {"ok": False, "detail": "compiler rejected the source"},
    )
    assert not result.done and not result.needs_finalization
    assert len(messages) == 3 and operations.count(("build", "")) == 1


async def test_green_explicit_check_is_reused_but_infrastructure_failure_is_not_completion():
    write = _repair_action("write_file", path=SCREEN, content="export default function Page() {}")
    ready, calls, ops = await scripted({}, [write, _repair_action("build"), DONE])
    assert ready.done and len(calls) == 3 and ops.count(("build", "")) == 1
    failed, calls, ops = await scripted(
        {},
        [write, DONE],
        check=lambda _: {
            "ok": False,
            "infra_dead": True,
            "detail": "Cell unavailable",
        },
    )
    assert not failed.done and failed.stop_reason == "infra_error"
    assert len(calls) == 2 and ops.count(("build", "")) == 1


async def test_an_explicit_green_build_does_not_cover_edits_while_sol_is_answering():
    checked = []

    def change_after_build(turn, files):
        if turn == 3:
            files[SCREEN] = "externally changed"

    def check(files):
        checked.append(files[SCREEN])
        return {"ok": files[SCREEN] == "good", "detail": "Expected good source"}

    result, calls, _ = await scripted(
        {}, [_repair_action("write_file", path=SCREEN, content="good"), _repair_action("build"),
             DONE, DONE],
        before_reply=change_after_build, check=check,
    )
    assert not result.done and not result.needs_finalization
    assert "source changed" in str(calls[3]).lower()
    assert checked == ["good", "externally changed"]


async def test_explicit_check_cannot_certify_a_tree_changed_during_the_command():
    checked = []

    def check(files):
        checked.append(files[SCREEN])
        files[SCREEN] = "changed during the command"
        return {"ok": checked[-1] == "good"}

    result, _, _ = await scripted(
        {}, [_repair_action("write_file", path=SCREEN, content="good"), _repair_action("build"),
             DONE], check=check,
    )
    assert not result.done and not result.needs_finalization
    assert checked == ["good", "changed during the command"]


async def test_sol_repair_includes_one_referenced_handler_without_extra_source_reads():
    screen = "export const make = () => fetch('/api/plan', {method:'POST'})"
    tree = {ROUTE: "export const POST = () => Response.json({message:'unavailable'})"}
    gap = "A submitted plan returns a message without meals: /api/plan."
    result, calls, ops = await scripted(
        tree,
        [
            _repair_action("write_file", path=SCREEN, content=screen),
            DONE,
            _repair_action(
                "edit_file", path=ROUTE, search="message:'unavailable'", replace="meals:['meal']"
            ),
            DONE,
        ],
        completion=lambda files, _: None if "meals:" in files[ROUTE] else gap,
    )
    assert result.done and len(calls) == 4
    evidence = next(
        m["content"] for m in calls[2] if "CURRENT_EDITABLE_SOURCE_JSON\n" in m["content"]
    )
    exact = json.loads(evidence.split("CURRENT_EDITABLE_SOURCE_JSON\n")[1])
    assert exact["path"] == SCREEN and exact["content"] == screen
    assert exact["related_route"]["path"] == ROUTE
    assert "message:'unavailable'" in exact["related_route"]["content"]
    assert all(action != "read_file" for action, _ in ops)


@pytest.mark.parametrize(
    "gap", ["Expected meals from /api/plan", "Failed in src/app/menu/page.tsx"]
)
def test_repair_context_prefers_diagnosed_screen_over_the_home_page(gap):
    screen = "src/app/menu/page.tsx"
    sources = {
        SCREEN: "export const Home = () => null",
        screen: "fetch('/api/plan')",
        ROUTE: "handler",
    }
    rendered, exact = ab._repair_source_context(SCREEN, sources, gap=gap)
    record = json.loads(rendered.split("CURRENT_EDITABLE_SOURCE_JSON\n")[1])
    assert exact and record["path"] == screen and record["related_route"]["path"] == ROUTE


@pytest.mark.parametrize("call", ["fetch('/api/plan' + extra)", "fetch(`/api/plan/${id}`)"])
def test_partial_dynamic_url_is_not_an_exact_route_witness(call):
    rendered, exact = ab._repair_source_context(
        SCREEN, {SCREEN: call, ROUTE: "handler"}, gap="plan",
    )
    assert exact and "related_route" not in rendered


@pytest.mark.parametrize(
    "handler",
    ["x" * ab.MAX_REPAIR_CONTEXT_BYTES, "sk-" + "s" * 32],
    ids=["combined-byte-limit", "credentials"],
)
def test_related_route_obeys_shared_byte_and_secret_bounds(handler):
    source = "export const plan = () => fetch('/api/plan')"
    rendered, valid = ab._repair_source_context(
        SCREEN, {SCREEN: source, ROUTE: handler}, gap="/api/plan"
    )
    assert valid and "related_route" not in rendered and handler not in rendered


@pytest.mark.parametrize("drift", [False, True], ids=["unchanged-source", "source-changed-offline"])
async def test_cold_auto_check_reconciles_the_paid_done_and_existing_command(drift):
    tree, calls, physical, build_keys = {}, [], [], []
    saved = receipt = None
    cold = False

    async def complete(*args, **kwargs):
        assert not cold, "The settled provider reply must not be repurchased."
        calls.append(True)
        return (
            DONE
            if len(calls) == 2
            else _repair_action("write_file", path=SCREEN, content="candidate")
        )

    async def provider(frame, invoke):
        nonlocal saved
        if cold:
            assert frame == saved
            return DONE
        saved = deepcopy(frame)
        return await invoke()

    async def tool(key, action, invoke):
        nonlocal receipt
        if action.name != "build":
            return await invoke()
        build_keys.append(key)
        if cold:
            assert receipt
            return receipt
        receipt = await invoke()
        raise GenerationYielded(
            run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency"
        )

    async def snapshot():
        return dict(tree)

    driver = SimpleNamespace(
        context={},
        restore_frame=lambda _: deepcopy(saved["value"]) if cold else None,
        provider=provider,
        tool=tool,
    )

    async def run():
        return await ab.run_agent_build(
            model=SOL_MODEL,
            system_prompt="sys",
            user_prompt="Create an app",
            complete=complete,
            execute=_ok_executor(physical, tree),
            snapshot_files=snapshot,
            coordinator_handoff=True,
            completion_check=lambda *_: None,
            max_steps=2,
            continuation=driver,
        )

    with pytest.raises(GenerationYielded):
        await run()
    cold = True
    if drift:
        tree[SCREEN] = "different candidate"
    result = await run()
    assert result.done is (not drift) and result.needs_finalization is (not drift)
    assert len(calls) == 2 and physical == [("write_file", SCREEN), ("build", "")]
    assert build_keys == ["legacy-action", "legacy-action"]
