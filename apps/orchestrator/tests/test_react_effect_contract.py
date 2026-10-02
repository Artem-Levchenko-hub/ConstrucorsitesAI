"""Real TypeScript AST checks for a reproduced React timer lifetime failure."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from yleum_orchestrator.services import machine_adapter

TS = Path(
    os.environ.get(
        "QA_TYPESCRIPT_PATH",
        str(Path(__file__).resolve().parents[3] / "apps/web/node_modules/typescript"),
    )
)

BROKEN = """import {useEffect,useState} from 'react';
export default function Page() {
 const [state,setState]=useState<'idle'|'loading'|'success'>('idle');
 useEffect(()=>{
   if(state!=='idle')return;
   setState('loading');
   const timer=setTimeout(()=>setState('success'),300);
   return()=>clearTimeout(timer);
 },[state]);
 return <main>{state}</main>;
}"""


def check(tmp_path, source, react=True):
    if not TS.exists():
        if os.environ.get("CI", "").lower() in {"1", "true", "yes"}:
            pytest.fail("CI requires the installed TypeScript parser at QA_TYPESCRIPT_PATH")
        pytest.skip("Installed TypeScript required for actual AST execution")
    root = tmp_path
    (root / "src").mkdir(exist_ok=True)
    (root / "src/page.tsx").write_text(source)
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18.3.1"} if react else {}})
    )
    (root / "node_modules").mkdir(exist_ok=True)
    (root / "node_modules/typescript").symlink_to(TS, target_is_directory=True)
    script = getattr(machine_adapter, "REACT_EFFECT_CONTRACT_JS", "")
    return subprocess.run(
        ["node", "-e", script], cwd=root, text=True, capture_output=True, timeout=10
    )


def test_known_self_dependent_cleanup_cancels_completion(tmp_path):
    result = check(tmp_path, BROKEN)
    assert result.returncode == 1, result.stdout
    assert "self-cancelling" in result.stdout
    assert "src/page.tsx" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        BROKEN.replace("},[state])", "},[])"),
        BROKEN.replace(
            "if(state!=='idle')return;\n   setState('loading');", "if(state!=='loading')return;"
        ),
        BROKEN.replace("return()=>clearTimeout(timer);", ""),
        BROKEN.replace("},[state])", "},[queryKey])"),
        BROKEN.replace("useEffect(()=>", "useEffect((state)=>"),
        BROKEN.replace("return()=>clearTimeout(timer);", "return(timer)=>clearTimeout(timer);"),
        BROKEN.replace("setState('loading');", "setState('loading'); throw Error('stop');"),
        "import {useEffect,useState} from 'react'; function Page(){"
        "const[state,setState]=useState('idle');useEffect(()=>{if(state!=='idle')return; "
        "fetch('/api/data').then(()=>setState('success'));},[state]);}",
        "import {useEffect,useState} from 'react'; function Page(){"
        "const[state,setState]=useState('idle');useEffect(()=>{if(state!=='idle')return; "
        "setTimeout(()=>setState('loading'),300);},[state]);}",
        BROKEN.replace("setState('loading');", "setState('idle');"),
        BROKEN.replace("from 'react'", "from './custom-hooks'"),
        BROKEN.replace("useEffect(()=>", "useEffect(()=> /* diagnostic */"),
    ],
)
def test_valid_effect_lifetimes_are_not_rejected(tmp_path, source):
    if "diagnostic" in source:
        # A comment cannot hide the bad semantics.
        assert check(tmp_path, source).returncode == 1
    else:
        assert check(tmp_path, source).returncode == 0


def test_aliases_and_react_namespace_are_parsed(tmp_path):
    source = BROKEN.replace("{useEffect,useState}", "{useEffect as effect,useState as stateHook}")
    source = source.replace("=useState<", "=stateHook<").replace(" useEffect(", " effect(")
    assert check(tmp_path, source).returncode == 1


def test_non_react_app_skips_checker(tmp_path):
    assert check(tmp_path, BROKEN, react=False).returncode == 0


@pytest.mark.parametrize("alias", ["React", "R"])
def test_react_namespace_hook_is_checked(tmp_path, alias):
    source = BROKEN.replace(
        "import {useEffect,useState} from 'react';", f"import * as {alias} from 'react';"
    )
    source = source.replace("=useState<", f"={alias}.useState<").replace(
        " useEffect(", f" {alias}.useEffect("
    )
    assert check(tmp_path, source).returncode == 1


@pytest.mark.parametrize(
    "argv", [["python", "-m", "pytest"], ["ruby", "test.rb"], ["go", "test", "./..."]]
)
def test_non_node_manifest_does_not_receive_node_command(argv):
    from types import SimpleNamespace

    assert not machine_adapter.node_manifest_commands(
        SimpleNamespace(tasks=[SimpleNamespace(argv=argv)])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,executable",
    [
        ("fast_check", "pnpm"),
        ("full_build", "pnpm"),
        ("fast_check", "python"),
        ("full_build", "ruby"),
        ("full_build", "go"),
    ],
)
async def test_controller_guard_is_scoped_and_finishes_failed_command(role, executable):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.core.project_machine import MachineManifest
    from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest

    commands = []
    finished = []

    class Machine:
        async def ensure(self, *args):
            pass

        async def request_start(self, *args, **kwargs):
            return None

        async def request_heartbeat(self, *args, **kwargs):
            pass

        async def exec_start(self, argv, cwd, mutation):
            commands.append((argv, cwd))
            return "owned-operation"

        async def exec_status(self, *args):
            return SimpleNamespace(
                state="completed", exit_code=1, output="self-cancelling React effect"
            )

        async def request_finish(self, mutation, result):
            finished.append(result)
            return result

    runtime = machine_adapter.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda _: (Machine(), object())
    runtime._migration_dependency_gap = AsyncMock(return_value=None)
    runtime._project_migrations = AsyncMock(return_value={})
    value = payload()
    value["tasks"].append({"name": "types", "role": role, "argv": [executable, "typecheck"]})
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:fast_check",
        task_role=role,
    )
    result = await runtime.execute(
        SimpleNamespace(), MachineManifest.model_validate(value), request
    )
    assert result.exit_code == 1
    assert len(commands) == 1
    if executable == "pnpm":
        assert commands[0] == (["node", "-e", machine_adapter.REACT_EFFECT_CONTRACT_JS], ".")
    else:
        assert commands[0] == ([executable, "typecheck"], ".")
    assert len(finished) == 1
    assert finished[0].state == "completed"
    assert "self-cancelling" in finished[0].output


def test_non_next_react_javascript_without_typescript_is_preserved(tmp_path):
    check(tmp_path, BROKEN)
    (tmp_path / "node_modules/typescript").unlink()
    result = subprocess.run(
        ["node", "-e", machine_adapter.REACT_EFFECT_CONTRACT_JS],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "not applicable" in result.stdout


@pytest.mark.parametrize(
    "shadow",
    [
        "function useEffect() {}",
        "function clearTimeout() {}",
        "class React {}",
        "const {useEffect} = customHooks;",
    ],
)
def test_lexically_shadowed_hooks_or_timer_cannot_prove_deadlock(tmp_path, shadow):
    source = BROKEN.replace(
        "export default function Page() {", "export default function Page() {" + shadow
    )
    if shadow == "class React {}":
        source = source.replace("{useEffect,useState}", "React")
        source = source.replace("=useState<", "=React.useState<").replace(
            " useEffect(", " React.useEffect("
        )
    result = check(tmp_path, source)
    assert result.returncode == 0, result.stdout


def test_destructured_hook_parameter_is_not_react_import(tmp_path):
    source = BROKEN.replace("function Page()", "function Page({useEffect})")
    assert check(tmp_path, source).returncode == 0


@pytest.mark.asyncio
async def test_outer_timeout_terminates_controller_child_before_finishing_request():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4, uuid5

    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.core.project_machine import MachineManifest
    from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest

    events = []
    child = [None]
    phase = [None]
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:fast_check",
        task_role="fast_check",
    )

    class Machine:
        async def ensure(self, *args):
            pass

        async def request_start(self, mutation, **kwargs):
            phase[0] = kwargs["phase"]
            return None

        async def request_heartbeat(self, mutation, **kwargs):
            phase[0] = kwargs["phase"]

        async def exec_start(self, argv, cwd, mutation):
            child[0] = str(mutation.operation_id)
            events.append("start")
            return child[0]

        async def exec_status(self, *args):
            raise TimeoutError("owned controller command timed out")

        async def inspect_request_status(self, **kwargs):
            return SimpleNamespace(result=None, state="running", phase=phase[0])

        async def exec_terminate(self, operation, mutation, *, grace_seconds):
            assert operation == child[0]
            assert mutation.operation_id == uuid5(request.operation_id, phase[0])
            child[0] = None
            events.append("terminate")

        async def request_finish(self, mutation, result):
            events.append("finish")
            return result

    runtime = machine_adapter.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda _: (Machine(), object())
    runtime._migration_dependency_gap = AsyncMock(return_value=None)
    value = payload()
    value["tasks"].append({"name": "types", "role": "fast_check", "argv": ["pnpm", "typecheck"]})
    result = await runtime.execute(
        SimpleNamespace(), MachineManifest.model_validate(value), request
    )
    assert phase[0] == "controller:react-effect-lifetime:v1"
    assert events == ["start", "terminate", "finish"]
    assert child[0] is None
    assert result.exit_code == 124 and result.timed_out


@pytest.mark.parametrize("wrapper", ["ancestor_hook", "module_timer"])
def test_enclosing_lexical_bindings_prevent_react_timer_certainty(tmp_path, wrapper):
    imports, body = BROKEN.split("export default ", 1)
    if wrapper == "ancestor_hook":
        source = imports + "function Wrapper(useEffect) { return " + body + "; }"
    else:
        source = BROKEN + "\nfunction clearTimeout() {}"
    assert check(tmp_path, source).returncode == 0


def test_ci_missing_parser_fails_instead_of_skipping(tmp_path, monkeypatch):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setitem(globals(), 'TS', tmp_path / 'missing-typescript')
    with pytest.raises(BaseException) as observed:
        check(tmp_path, BROKEN)
    assert isinstance(observed.value, pytest.fail.Exception)
    assert "CI requires the installed TypeScript parser" in str(observed.value)
