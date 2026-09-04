"""Tests for the native tool-use build loop (services/agent_native).

Covers: `_module_not_found_hint` (anti-hallucination recovery), the
EXPLORE-STALL no-write guard (nudge → abort as 'exploring'), and the infra
circuit breaker (container/orchestrator dead → abort as 'infra_error' instead of
grinding the step budget — the 2026-07-08 hibernate-mid-build incident).
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from omnia_api.services import agent_native
from omnia_api.services.agent_builder import AgentResult
from omnia_api.services.agent_native import NativeMessagesAttemptAuth, _module_not_found_hint

_RUNNER_SRC = Path(__file__).resolve().parents[2] / "agent-runner" / "src"
_GATEWAY_SRC = Path(__file__).resolve().parents[2] / "llm-gateway" / "src"


def _load_cross_package_runner_auth() -> tuple[Any, Any, Any, Any, Any, Any]:
    for extra_src in (_RUNNER_SRC, _GATEWAY_SRC):
        extra_src_str = str(extra_src)
        if extra_src_str not in sys.path:
            sys.path.append(extra_src_str)

    runner_pkg = importlib.import_module("omnia_agent_runner")
    gateway_config = importlib.import_module("omnia_gateway.core.config")
    gateway_runner_auth = importlib.import_module("omnia_gateway.core.runner_auth")
    return (
        runner_pkg.HS256JWTSigner,
        runner_pkg.ProjectCellJWTMessagesAuth,
        runner_pkg.RunnerIdentity,
        gateway_config.Settings,
        gateway_runner_auth.RunnerReplayError,
        gateway_runner_auth,
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    from omnia_api.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_native_agent_uses_sonnet_while_autoheal_keeps_gemini() -> None:
    from omnia_api.services import autoheal

    assert agent_native._MODEL == "claude-sonnet-5"
    assert autoheal._HEAL_MODEL == "gemini-3.1-pro-preview-customtools"


def test_max_native_prompt_disables_incompatible_generic_proof_tools() -> None:
    prompt = agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT\nBuild the app")

    assert "takes precedence" in prompt
    assert "Do NOT call or retry probe/verify_isolation" in prompt
    assert "run runtime_check after the final write" in prompt
    assert "signed MAX preview session" in prompt
    assert "DESIGN.md" in prompt
    assert "утилитарный mobile product" in prompt
    assert "НЕ landing page" in prompt
    assert "Awwwards-риторику" in prompt
    assert "ОРКЕСТРАЦИЯ МОДЕЛЕЙ" not in prompt
    assert "МЕДИА (картинки + КИНО-ВИДЕО)" not in prompt
    assert "ЖИВЫЕ МИКРО-ВЗАИМОДЕЙСТВИЯ" not in prompt
    assert "картинка «летит» при прокрутке" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["bash_ok", "bash_failure", "bash_timeout", "failed_probe", "build"]
)
async def test_machine_mutation_or_reverification_cannot_reuse_old_proof(monkeypatch, mutation):
    turns = iter(
        [
            _turn(("build", {}), ("runtime_check", {"path": "/"})),
            _turn(
                (
                    "runtime_check"
                    if mutation == "failed_probe"
                    else "build"
                    if mutation == "build"
                    else "bash",
                    {"cmd": "kill product"},
                )
            ),
            _turn(("done", {"summary": "STALE_PROOF"})),
        ]
    )

    async def fake_call(*args, **kwargs):
        try:
            return next(turns)
        except StopIteration:
            raise RuntimeError("authored fixture ends here")

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    calls = {"build": 0, "runtime_check": 0}

    async def execute(action):
        if action.name == "bash":
            return {
                "ok": mutation == "bash_ok",
                "environment_mutated": True,
                "detail": "partial mutation, no source diff",
            }
        calls[action.name] = calls.get(action.name, 0) + 1
        # Subsequent local auto-verification cannot turn this negative fixture green.
        return {
            "ok": calls[action.name] == 1,
            **({"environment_mutated": True} if action.name == "build" else {}),
        }

    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE",
        task="authored test only",
        execute=execute,
        initial_files={"src/app/page.tsx": "existing product"},
        max_steps=4,
        completion_check=lambda files, evidence: (
            None if evidence.get("runtime_check_after_write") else "fresh runtime_check required"
        ),
    )
    assert result.done is False
    assert not result.evidence.get("runtime_check_after_write")


def test_segment_merge_does_not_resurrect_invalidated_proofs_without_source_diff():
    evidence = agent_native._merge_segment_evidence(
        {"build_after_write": 1, "runtime_check_after_write": 1},
        {"build_after_write": 0, "runtime_check_after_write": 0},
        wrote_files=False,
    )
    assert evidence["build_after_write"] == 0
    assert evidence["runtime_check_after_write"] == 0


def test_max_native_toolset_removes_incompatible_proof_and_landing_noise() -> None:
    names = [tool["name"] for tool in agent_native._MAX_TOOLS_CACHED]

    assert "bash" not in names
    assert "probe" not in names
    assert "verify_isolation" not in names
    assert "runtime_check" in names
    assert "see" not in names
    assert "generate_media" in names
    assert "done" in names
    assert agent_native._MAX_TOOLS_CACHED[-1]["cache_control"] == agent_native._CACHE

    descriptions = "\n".join(str(tool["description"]) for tool in agent_native._MAX_TOOLS_CACHED)
    assert "hero too small" not in descriptions
    assert "KEYFRAME" not in descriptions
    assert "scroll-scrub" not in descriptions


def test_max_native_toolset_can_opt_into_project_shell() -> None:
    names = [tool["name"] for tool in agent_native._MAX_TOOLS_WITH_BASH_CACHED]

    assert "bash" in names
    assert "probe" not in names
    assert "verify_isolation" not in names
    bash_tool = next(
        tool for tool in agent_native._MAX_TOOLS_WITH_BASH_CACHED if tool["name"] == "bash"
    )
    assert "isolated MAX project sandbox" in str(bash_tool["description"])


def test_first_max_build_has_no_template_and_cannot_finish_at_core_stage() -> None:
    """The verified core is a seed, never a replacement for the Google agent."""
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "omnia_api"
        / "routers"
        / "messages.py"
    ).read_text(encoding="utf-8")

    assert 'stop_reason="deterministic_template"' not in source
    assert "_merge_seeded_agent_files" in source
    assert "agent_native.run_native_build" in source
    assert "build_max_product_contract" in source
    assert "max_completion_gap" in source
    assert "completion_check=_completion_check" in source
    assert "_agent_step_budget" in source
    assert "configured_steps=_agent_steps" in source
    assert "max_steps=_agent_steps" in source
    assert "max_segments=_native_max_segments" in source
    assert "_agent_res.segments" in source
    assert '"autonomous_recovery"' not in source
    assert "max_source_completion_gap" not in source
    assert "_seg < 2" not in source
    assert "_first_max_without_product" in source
    assert "func.length(func.trim(Snapshot.prompt_text)) > 0" in source
    assert '_bounded_stop and project_template != "max_miniapp"' in source
    assert "MAX_SECURITY_LOCKED_FILES" in source
    assert "MAX_MODEL_LOCKED_FILES" in source
    assert "Direct DB access is forbidden in MAX product files." in source
    assert "agent_sandbox_capabilities" in source
    assert "base_workspace_revision" in source
    assert "pnpm_lockfile" in source
    assert "unsafe_max_backend_paths" in source
    assert "max_model_write_rejection" in source
    assert "_project_cell_runtime_check" in source
    assert "_recover_max_resume_prompt" in source
    assert '_starter_patch = {**_starter_files, "src/app/page.tsx": ""}' in source
    assert '"rm -f -- src/app/page.tsx"' not in source
    assert "{} if not _max_has_generated_snapshot else dict(current_files)" in source
    assert "normalize_max_globals_css" in source
    assert "seed_design_memory" in source
    assert "await asyncio.sleep(2)" in source
    assert "max_project_shell_enabled" in source
    assert "MAX project shell is locked" in source
    assert "Shell mutation is disabled for MAX projects." not in source


def test_first_max_native_build_has_bounded_automatic_continuation_default() -> None:
    config_source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "omnia_api"
        / "core"
        / "config.py"
    ).read_text(encoding="utf-8")

    assert "agent_max_segments: int = Field(default=4, ge=1, le=8)" in config_source


def test_max_guardrail_checks_final_tree_and_rolls_back_unsafe_backend() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "omnia_api"
        / "routers"
        / "messages.py"
    ).read_text(encoding="utf-8")

    assert "for path, content in {**current_files, **files}.items()" in source
    assert "path: current_files.get(path, \"\") for path in rollback_paths" in source
    assert "await orchestrator_client.hot_reload(" in source
    assert "files.clear()" in source
    assert "files.update(rollback_files)" in source
    assert '"unsafe_generated_backend"' in source
    assert "except ApiError:" in source
    assert "except Exception as _guard_exc" in source
    verdict_source = source[
        source.index("def _backend_verdict()") : source.index("_guard_attempt = 0")
    ]
    assert 'if project_template == "max_miniapp":' in verdict_source
    assert "and _max_shell_enabled" not in verdict_source


def test_abort_unsafe_max_backend_rolls_back_new_file_before_rejecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.core.errors import ApiError
    from omnia_api.routers import messages

    calls: list[dict[str, Any]] = []

    async def _hot_reload(project_id, slug, files, *, empty_files=()):
        assert empty_files == ()
        calls.append(
            {
                "project_id": project_id,
                "slug": slug,
                "files": dict(files),
            }
        )
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", _hot_reload)
    generated = {
        "src/app/page.tsx": "partial UI",
        "src/app/api/report/route.js": "unsafe",
    }

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            messages._abort_unsafe_max_backend(
                project_id=UUID(int=1),
                project_slug="max-app",
                current_files={"src/app/page.tsx": "safe UI"},
                files=generated,
                unsafe_paths=["src/app/api/report/route.js"],
            )
        )

    assert exc.value.code == "unsafe_generated_backend"
    assert exc.value.status_code == 422
    assert generated == {
        "src/app/page.tsx": "safe UI",
        "src/app/api/report/route.js": "",
    }
    assert calls == [
        {
            "project_id": UUID(int=1),
            "slug": "max-app",
            "files": {
                "src/app/api/report/route.js": "",
                "src/app/page.tsx": "safe UI",
            },
        }
    ]


def test_abort_unsafe_max_backend_still_blocks_if_live_rollback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.core.errors import ApiError
    from omnia_api.routers import messages

    async def _hot_reload(project_id, slug, files, *, empty_files=()):
        assert empty_files == ()
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", _hot_reload)
    generated = {
        "src/app/page.tsx": "partial UI",
        "src/app/api/report/route.js": "unsafe",
    }

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            messages._abort_unsafe_max_backend(
                project_id=UUID(int=2),
                project_slug="max-app",
                current_files={"src/app/page.tsx": "safe UI"},
                files=generated,
                unsafe_paths=["src/app/api/report/route.js"],
            )
        )

    assert exc.value.code == "unsafe_generated_backend"
    assert "Live preview rollback also failed" in exc.value.message
    assert generated == {
        "src/app/page.tsx": "safe UI",
        "src/app/api/report/route.js": "",
    }


def _load_messages_helpers(*function_names: str) -> dict[str, Any]:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "omnia_api"
        / "routers"
        / "messages.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source, filename="messages.py")
    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_CONTINUE_KEYWORDS"
            for target in node.targets
        ):
            body.append(node)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_CONTINUE_KEYWORDS"
        ):
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            body.append(node)
    namespace: dict[str, Any] = {"Sequence": Sequence, "Any": Any}
    code = compile(
        ast.Module(body=body, type_ignores=[]),
        "messages_resume_helpers",
        "exec",
    )
    exec(code, namespace)
    return namespace


def test_failed_max_resume_recovers_the_original_brief() -> None:
    _recover_max_resume_prompt = _load_messages_helpers(
        "_is_continue_request",
        "_recover_max_resume_prompt",
    )["_recover_max_resume_prompt"]

    assert (
        _recover_max_resume_prompt(
            ["продолжи", "Продолжай сборку", "Собери фитнес-тренера с ИИ и статистикой"]
        )
        == "Собери фитнес-тренера с ИИ и статистикой"
    )
    assert _recover_max_resume_prompt(["продолжи", "доделай"]) is None


def test_rolled_back_max_generation_is_never_reported_as_done() -> None:
    from omnia_api.services.agent_builder import AgentResult

    _agent_result_message = _load_messages_helpers("_agent_result_message")["_agent_result_message"]

    result = AgentResult(
        done=False,
        summary="Первая генерация не завершена; оставлена безопасная основа.",
        files={},
        steps=30,
        stop_reason="core_only_rolled_back",
    )

    assert _agent_result_message(result, is_edit=False).startswith("Первая генерация не завершена")


def test_seeded_max_files_are_committed_with_agent_customisations() -> None:
    _merge_seeded_agent_files = _load_messages_helpers(
        "_merge_seeded_agent_files"
    )["_merge_seeded_agent_files"]

    starter = {"src/app/page.tsx": "starter", "src/app/globals.css": "safe css"}
    generated = {"src/app/page.tsx": "google agent result", "src/components/Profile.tsx": "ui"}

    merged = _merge_seeded_agent_files(starter, generated)

    assert merged == {
        "src/app/page.tsx": "google agent result",
        "src/app/globals.css": "safe css",
        "src/components/Profile.tsx": "ui",
    }
    assert starter["src/app/page.tsx"] == "starter"


def test_hint_none_on_clean_or_unrelated_error() -> None:
    assert _module_not_found_hint("") is None
    assert _module_not_found_hint("Build succeeded, 0 errors") is None
    # a real error that is NOT a missing @/ module → no hint (don't over-fire)
    assert (
        _module_not_found_hint("src/app/page.tsx(3,10): error TS2345: Argument of type 'string'")
        is None
    )
    # a bare package (not an @/ alias) is a dependency problem, not the
    # SDK-hallucination this hint addresses → stay silent.
    assert _module_not_found_hint("Cannot find module 'postgres'") is None


def test_hint_fires_on_ts2307_internal_alias() -> None:
    out = _module_not_found_hint(
        "src/lib/sdk/tasks.ts(4,24): error TS2307: Cannot find module "
        "'@/lib/entities/engine' or its corresponding type declarations."
    )
    assert out is not None
    assert "@/lib/entities/engine" in out
    assert "do not create" in out.lower()
    # steers away from fabricating an SDK/engine wrapper
    assert "sdk" in out.lower() and "engine" in out.lower()


def test_hint_dedupes_and_caps_modules() -> None:
    blob = "\n".join(
        f"src/a{i}.ts: error TS2307: Cannot find module '@/lib/m{i}'" for i in range(8)
    )
    blob += "\nsrc/z.ts: error TS2307: Cannot find module '@/lib/m0'"  # repeat
    out = _module_not_found_hint(blob)
    assert out is not None
    assert out.count("@/lib/m0") == 1  # deduped
    listed = [m for m in (f"@/lib/m{i}" for i in range(8)) if m in out]
    assert len(listed) == 5  # capped to 5


# ── run_native_build loop guards (stubbed LLM + executor) ────────────────────


@pytest.mark.asyncio
async def test_native_segments_preserve_files_and_completion_evidence() -> None:
    calls: list[str] = []

    def completion_check(files: Mapping[str, str], evidence: Mapping[str, int]) -> str | None:
        if "src/app/page.tsx" not in files:
            return "missing page"
        if evidence.get("runtime_check_after_write", 0) < 1:
            return "missing runtime"
        if evidence.get("probe_after_write", 0) < 1:
            return "missing signed preview"
        return None

    async def run_segment(task: str, check: Any, _initial_files: Mapping[str, str]) -> AgentResult:
        calls.append(task)
        if len(calls) == 1:
            assert check(
                {"src/app/page.tsx": "export default function Page() { return <main /> }"},
                {"runtime_check_after_write": 1},
            ) == "missing signed preview"
            return AgentResult(
                done=False,
                summary="missing signed preview",
                files={"src/app/page.tsx": "export default function Page() { return <main /> }"},
                steps=40,
                transcript=[{"role": "assistant", "content": "implemented"}],
                stop_reason="max_steps",
                evidence={"runtime_check_after_write": 1},
            )
        assert "same GenerationRun" in task
        assert check({}, {"probe_after_write": 1}) is None
        return AgentResult(
            done=True,
            summary="complete",
            files={},
            steps=2,
            transcript=[{"role": "assistant", "content": "verified"}],
            stop_reason="done",
            evidence={"probe_after_write": 1},
        )

    result = await agent_native._run_native_segments(
        task="Build the requested app",
        completion_check=completion_check,
        max_segments=4,
        run_segment=run_segment,
    )

    assert result.done is True
    assert result.files == {
        "src/app/page.tsx": "export default function Page() { return <main /> }"
    }
    assert result.evidence["runtime_check_after_write"] == 1
    assert result.evidence["probe_after_write"] == 1
    assert result.steps == 42
    assert len(result.transcript) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_native_segment_write_invalidates_all_prior_after_write_proof() -> None:
    calls = 0

    def completion_check(files: Mapping[str, str], evidence: Mapping[str, int]) -> str | None:
        if "src/app/page.tsx" not in files or "src/components/Product.tsx" not in files:
            return "missing product file"
        if evidence.get("build_after_write", 0) < 1:
            return "missing fresh build"
        if evidence.get("runtime_check_after_write", 0) < 1:
            return "missing fresh runtime"
        if evidence.get("probe_after_write", 0) < 1:
            return "missing fresh signed preview"
        return None

    async def run_segment(
        _task: str,
        check: Any,
        _initial_files: Mapping[str, str],
    ) -> AgentResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            page = {"src/app/page.tsx": "export default function Page() { return <main /> }"}
            assert check(
                page,
                {
                    "build_after_write": 1,
                    "runtime_check_after_write": 1,
                    "probe_after_write": 1,
                },
            ) == "missing product file"
            return AgentResult(
                done=False,
                summary="product breadth remains incomplete",
                files=page,
                steps=40,
                stop_reason="max_steps",
                evidence={
                    "build_after_write": 1,
                    "runtime_check_after_write": 1,
                    "probe_after_write": 1,
                },
            )

        component = {
            "src/components/Product.tsx": "export function Product() { return <section /> }"
        }
        assert check(component, {}) == "missing fresh build"
        assert check(component, {"build_after_write": 1}) == "missing fresh runtime"
        assert check(
            component,
            {"build_after_write": 1, "runtime_check_after_write": 1},
        ) == "missing fresh signed preview"
        fresh_proof = {
            "build_after_write": 1,
            "runtime_check_after_write": 1,
            "probe_after_write": 1,
        }
        assert check(component, fresh_proof) is None
        return AgentResult(
            done=True,
            summary="complete after fresh proof",
            files=component,
            steps=4,
            stop_reason="done",
            evidence=fresh_proof,
        )

    result = await agent_native._run_native_segments(
        task="Build the complete product",
        completion_check=completion_check,
        max_segments=4,
        run_segment=run_segment,
    )

    assert calls == 2
    assert result.done is True
    assert result.evidence == {
        "build_after_write": 1,
        "runtime_check_after_write": 1,
        "probe_after_write": 1,
    }


@pytest.mark.asyncio
async def test_native_segments_stop_after_proven_no_progress() -> None:
    calls = 0

    async def run_segment(
        _task: str,
        _check: Any,
        _initial_files: Mapping[str, str],
    ) -> AgentResult:
        nonlocal calls
        calls += 1
        return AgentResult(
            done=False,
            summary="still exploring",
            files={},
            steps=12,
            stop_reason="exploring",
            evidence={},
        )

    result = await agent_native._run_native_segments(
        task="Build it",
        completion_check=lambda _files, _evidence: "missing product",
        max_segments=4,
        run_segment=run_segment,
    )

    assert calls == 1
    assert result.done is False
    assert result.stop_reason == "no_progress"


@pytest.mark.asyncio
async def test_native_segments_honour_cancellation_between_segments() -> None:
    calls = 0
    owner_task = asyncio.current_task()
    assert owner_task is not None

    async def run_segment(
        _task: str,
        _check: Any,
        _initial_files: Mapping[str, str],
    ) -> AgentResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            asyncio.get_running_loop().call_soon(owner_task.cancel)
            return AgentResult(
                done=False,
                summary="more work remains",
                files={"src/app/page.tsx": "changed"},
                steps=40,
                stop_reason="max_steps",
                evidence={"build_after_write": 1},
            )
        raise AssertionError("cancelled generation started another provider segment")

    with pytest.raises(asyncio.CancelledError):
        await agent_native._run_native_segments(
            task="Build it",
            completion_check=lambda _files, _evidence: "more work remains",
            max_segments=4,
            run_segment=run_segment,
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_native_default_stays_single_segment_for_legacy_and_non_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_segment(**_kwargs: object) -> AgentResult:
        nonlocal calls
        calls += 1
        return AgentResult(
            done=False,
            summary="bounded legacy result",
            files={"index.html": "changed"},
            steps=2,
            stop_reason="max_steps",
        )

    monkeypatch.setattr(agent_native, "_run_native_segment", fake_segment)
    result = await agent_native.run_native_build(
        system="ordinary web app",
        task="Build it",
        execute=lambda _action: asyncio.sleep(0, result={"ok": True}),
    )

    assert calls == 1
    assert result.stop_reason == "max_steps"


@pytest.mark.asyncio
async def test_native_continuation_reuses_exact_generation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, object, object, object]] = []

    async def fake_segment(**kwargs: object) -> AgentResult:
        calls.append(
            (
                kwargs["user_id"],
                kwargs["project_id"],
                kwargs["run_id"],
                kwargs["message_id"],
            )
        )
        if len(calls) == 1:
            return AgentResult(
                done=False,
                summary="continue",
                files={"src/app/page.tsx": "changed"},
                steps=40,
                stop_reason="max_steps",
                evidence={"build_after_write": 1},
            )
        return AgentResult(done=True, summary="done", files={}, steps=1, stop_reason="done")

    monkeypatch.setattr(agent_native, "_run_native_segment", fake_segment)
    await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="Build it",
        execute=lambda _action: asyncio.sleep(0, result={"ok": True}),
        user_id="user-1",
        project_id="project-1",
        run_id="run-1",
        message_id="message-1",
        max_segments=4,
        completion_check=lambda _files, _evidence: None,
    )

    assert calls == [
        ("user-1", "project-1", "run-1", "message-1"),
        ("user-1", "project-1", "run-1", "message-1"),
    ]


def _turn(*tools: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """An Anthropic-shaped assistant turn with the given tool_use blocks."""
    return {
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": f"tu_{i}", "name": name, "input": args}
            for i, (name, args) in enumerate(tools)
        ],
    }


@pytest.mark.asyncio
async def test_native_max_step_segment_continues_and_completes_real_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_turns = [
        _turn(
            (
                "write_file",
                {
                    "path": "src/app/page.tsx",
                    "content": "export default function Page() { return <main /> }",
                },
            )
        ),
        _turn(
            (
                "write_file",
                {
                    "path": "src/components/Product.tsx",
                    "content": "export function Product() { return <section /> }",
                },
            )
        ),
    ]

    async def fake_call(
        _client: Any,
        _url: str,
        _convo: Any,
        _system: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return provider_turns.pop(0)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(_action: Any) -> dict[str, Any]:
        return {"ok": True}

    def completion_check(files: Mapping[str, str], evidence: Mapping[str, int]) -> str | None:
        if "src/app/page.tsx" not in files or "src/components/Product.tsx" not in files:
            return "product breadth remains incomplete"
        if evidence.get("build_after_write", 0) < 1:
            return "run build after the final write"
        return None

    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="Build the complete product",
        execute=execute,
        completion_check=completion_check,
        max_steps=1,
        max_segments=4,
    )

    assert result.done is True
    assert result.stop_reason == "max_steps_green"
    assert result.segments == 2
    assert result.steps == 2
    assert set(result.files) == {"src/app/page.tsx", "src/components/Product.tsx"}
    assert not provider_turns


@pytest.mark.asyncio
async def test_native_continuation_does_not_force_rewrite_existing_max_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(
        _client: Any,
        _url: str,
        _convo: Any,
        _system: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        assert kwargs["tools"] == agent_native._MAX_TOOLS_CACHED
        return _turn(("read_file", {"path": "src/app/page.tsx"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(_action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "existing product"}

    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE",
        task="Continue",
        execute=execute,
        completion_check=lambda _files, _evidence: "more product work remains",
        initial_files={"src/app/page.tsx": "existing product"},
        max_steps=agent_native._NO_WRITE_ABORT_AT,
    )

    assert calls == agent_native._NO_WRITE_ABORT_AT
    assert result.stop_reason == "exploring"


@pytest.mark.asyncio
async def test_native_infra_breaker_aborts_after_dead_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container dead → every op infra_dead → abort in _INFRA_DEAD_ABORT_AT turns
    (the 2026-07-08 regression: it used to grind the whole step budget)."""
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        return _turn(("read_file", {"path": "a.ts"}), ("list_dir", {"path": "."}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": False, "error": "infra: Orchestrator returned 500", "infra_dead": True}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=40,
    )
    assert res.stop_reason == "infra_error"
    assert "unreachable" in res.summary
    assert calls["n"] == agent_native._INFRA_DEAD_ABORT_AT  # aborted, not ground out


@pytest.mark.asyncio
async def test_native_no_write_guard_nudges_then_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endless successful READS (no writes) → nudge from turn 6, abort at 12 as
    'exploring' — messages.py's honest-result branches consume that."""

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "file body"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=40,
    )
    assert res.stop_reason == "exploring"
    assert res.steps == agent_native._NO_WRITE_ABORT_AT
    assert "[LOOP GUARD]" in str(res.transcript)  # the nudge actually landed


@pytest.mark.asyncio
async def test_first_max_build_locks_discovery_tools_until_first_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After discovery closes, the next provider turn is forced to write the
    fixed MAX entry instead of spending another paid turn on a rejected read."""

    calls = {"n": 0}
    executed_reads = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] <= agent_native._MAX_PREWRITE_DISCOVERY_TURNS:
            assert kwargs["tools"] == agent_native._MAX_TOOLS_CACHED
            assert kwargs["tool_choice"] is None
            return _turn(("read_file", {"path": "src/app/layout.tsx"}))
        if calls["n"] == agent_native._MAX_PREWRITE_DISCOVERY_TURNS + 1:
            forced_tools = kwargs["tools"]
            assert [tool["name"] for tool in forced_tools] == ["write_file"]
            assert forced_tools[0]["input_schema"]["properties"]["path"] == {
                "type": "string",
                "enum": ["src/app/page.tsx"],
            }
            assert forced_tools[0]["description"] == agent_native._MAX_ENTRY_WRITE_GUIDANCE
            assert "self-contained src/app/page.tsx" in str(convo[-1])
            assert "do not import product components" in str(convo[-1])
            assert kwargs["tool_choice"] == {"type": "tool", "name": "write_file"}
            return _turn(
                ("write_file", {"path": "src/app/page.tsx", "content": "export default 1"})
            )
        if calls["n"] == agent_native._MAX_PREWRITE_DISCOVERY_TURNS + 2:
            assert kwargs["tools"] == agent_native._MAX_TOOLS_CACHED
            assert kwargs["tool_choice"] is None
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads
        if action.name == "read_file":
            executed_reads += 1
            return {"ok": True, "content": "platform core"}
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda written, evidence: None if written else "write product files",
        max_steps=40,
    )

    assert res.done is True
    assert res.files == {"src/app/page.tsx": "export default 1"}
    assert executed_reads == agent_native._MAX_PREWRITE_DISCOVERY_TURNS
    assert calls["n"] == agent_native._MAX_PREWRITE_DISCOVERY_TURNS + 3
    assert agent_native._MAX_PREWRITE_LOCK_RESULT in str(res.transcript)


@pytest.mark.asyncio
async def test_first_max_build_can_expose_bash_when_project_shell_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        assert kwargs["tools"] == agent_native._MAX_TOOLS_WITH_BASH_CACHED
        assert any(tool["name"] == "bash" for tool in kwargs["tools"])
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        actions.append(action.name)
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda files, evidence: None,
        allow_max_bash=True,
        max_steps=2,
    )

    assert res.done is True
    assert res.stop_reason == "max_steps_green"
    assert actions == ["build"]


@pytest.mark.asyncio
async def test_max_auxiliary_write_does_not_unlock_product_entry_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One helper write is not a product. The next mutation must create the
    required user-facing entry instead of extending support code indefinitely."""
    turns = iter(
        [
            _turn(("write_file", {"path": "src/lib/helper.ts", "content": "export const x=1"})),
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "export default 1"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "Готово"})),
        ]
    )

    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            assert kwargs["tools"] == agent_native._MAX_TOOLS_CACHED
            assert kwargs["tool_choice"] is None
        elif calls["n"] == 2:
            assert [tool["name"] for tool in kwargs["tools"]] == ["write_file"]
            assert kwargs["tools"][0]["description"] == agent_native._MAX_ENTRY_WRITE_GUIDANCE
            assert kwargs["tool_choice"] == {"type": "tool", "name": "write_file"}
            assert agent_native._MAX_PRODUCT_ENTRY_REQUIRED_RESULT in str(convo[-1])
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    executed: list[tuple[str, str]] = []

    async def execute(action: Any) -> dict[str, Any]:
        executed.append((action.name, action.path))
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda files, evidence: (
            None if "src/app/page.tsx" in files else "Create src/app/page.tsx."
        ),
        max_steps=10,
    )

    assert res.done is True
    assert ("write_file", "src/lib/helper.ts") in executed
    assert ("write_file", "src/lib/second.ts") not in executed
    assert ("read_file", "package.json") not in executed
    assert ("write_file", "src/app/page.tsx") in executed
    assert agent_native._MAX_PRODUCT_ENTRY_REQUIRED_RESULT in str(res.transcript)
    assert agent_native._MAX_ENTRY_WRITE_GUIDANCE in str(res.transcript)


@pytest.mark.asyncio
async def test_max_auxiliary_rewrites_are_not_product_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the same helper forever must hit the bounded no-progress stop."""
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"}))
        return _turn(
            (
                "write_file",
                {"path": "src/lib/helper.ts", "content": f"export const n={calls['n']}"},
            )
        )

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "written"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda files, evidence: None,
        max_steps=40,
    )

    assert res.stop_reason == "exploring"
    assert "without user-facing product progress" in res.summary
    assert calls["n"] == 1 + agent_native._NO_WRITE_ABORT_AT


@pytest.mark.asyncio
async def test_native_write_resets_no_write_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write every few turns keeps the streak below the abort threshold —
    the guard must not fire on a normally-working build."""
    counter = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] % 5 == 0:
            return _turn(("write_file", {"path": f"f{counter['n']}.ts", "content": "x"}))
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "body"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=15,
    )
    assert res.stop_reason == "max_steps_green"  # never tripped the exploring abort
    assert len(res.files) == 3  # the three successful writes were tracked


@pytest.mark.asyncio
async def test_native_edit_file_counts_as_write_and_lands_in_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edit_file success resets the streak AND its post-edit content is tracked
    (closes the gap where only write_file dirtied the done fact-gate)."""
    counter = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] == 1:
            return _turn(("edit_file", {"path": "e.ts", "search": "a", "replace": "b"}))
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "edit_file":
            return {"ok": True, "content": "post-edit content", "detail": "patched e.ts"}
        return {"ok": True, "content": "body"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=5,
    )
    assert res.stop_reason == "max_steps_green"
    assert res.files == {"e.ts": "post-edit content"}


@pytest.mark.asyncio
async def test_non_max_file_keys_are_not_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAX entry matching may normalize a path, but other stacks must preserve
    the executor's exact file key (especially leading-dot config files)."""
    turns = iter(
        [
            _turn(("write_file", {"path": ".config", "content": "exact"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "done"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="generic stack",
        task="write config",
        execute=execute,
        max_steps=5,
    )

    assert res.done is True
    assert res.files == {".config": "exact"}


@pytest.mark.asyncio
async def test_native_completion_check_rejects_thin_done_and_keeps_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean compiler result cannot bypass a product-specific fidelity gate."""
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "too early"})),
            _turn(
                (
                    "write_file",
                    {"path": "src/components/Feature.tsx", "content": "feature"},
                )
            ),
            _turn(("build", {})),
            _turn(("done", {"summary": "complete"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    def check(files: Any, evidence: Any) -> str | None:
        return None if len(files) >= 2 else "Need a real feature component."

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        completion_check=check,
        max_steps=8,
    )

    assert res.done is True
    assert res.summary == "complete"
    assert set(res.files) == {"src/app/page.tsx", "src/components/Feature.tsx"}
    assert "Need a real feature component" in str(res.transcript)


@pytest.mark.asyncio
async def test_native_hard_stop_runs_missing_local_proofs_before_shipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green tree must not fail only because the provider turn ended before
    deterministic runtime and persistence proof tool calls."""

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    actions: list[str] = []

    async def execute(action: Any) -> dict[str, Any]:
        actions.append(action.name)
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "green",
        }

    def check(files: Any, evidence: Any) -> str | None:
        assert "src/app/page.tsx" in files
        if evidence.get("runtime_check_after_write", 0) < 1:
            return "Run runtime_check after the last source write."
        if evidence.get("probe_after_write", 0) < 1:
            return "Run probe after the last source write."
        if evidence.get("probe", 0) < 2:
            return "Run a second probe for persistence readback."
        return None

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        completion_check=check,
        max_steps=1,
    )

    assert res.done is True
    assert res.stop_reason == "max_steps_green"
    assert actions == ["write_file", "build", "runtime_check", "probe", "probe"]


@pytest.mark.asyncio
async def test_native_tool_call_emits_chat_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every executed native tool remains visible through the agent.step WS path."""
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return _turn(("write_file", {"path": "src/app.ts", "content": "ok"}))
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Готово"}]}

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "written" if action.name != "build" else "clean",
        }

    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        emit=emit,
        max_steps=5,
    )

    progress = [data for event_type, data in events if event_type == "agent.step"]
    assert len(progress) == 2
    assert progress[0]["step"] == 0
    assert progress[0]["action"] == "write_file"
    assert progress[0]["path"] == "src/app.ts"
    assert "ok" in progress[0]["detail"]
    assert progress[0]["ok"] is True


@pytest.mark.asyncio
async def test_native_proseless_done_gets_human_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """end_turn with NO text must not leak "(no tool call)" into the chat —
    the summary becomes the user-visible assistant message (observed live)."""

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {"stop_reason": "end_turn", "content": []}  # prose-less finish

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=5,
    )
    assert res.done is True
    assert res.stop_reason == "max_steps_green"
    assert "(no tool call)" not in res.summary
    assert "Готово" in res.summary


@pytest.mark.asyncio
async def test_native_hard_clamps_legacy_limit_and_forwards_trace_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(kwargs)
        number = len(calls)
        if number % 5 == 0:
            return _turn(("write_file", {"path": f"f{number}.ts", "content": "ok"}))
        return _turn(("read_file", {"path": "src/app.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        user_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        run_id="33333333-3333-3333-3333-333333333333",
        message_id="44444444-4444-4444-4444-444444444444",
        max_steps=120,
    )

    assert len(calls) == agent_native._HARD_MAX_STEPS == 40
    assert res.stop_reason == "max_steps_green"
    assert calls[0]["stage"] == "build_plan"
    assert all(call["project_id"] == "22222222-2222-2222-2222-222222222222" for call in calls)
    assert all(call["run_id"] == "33333333-3333-3333-3333-333333333333" for call in calls)
    assert all(call["user_id"] == "11111111-1111-1111-1111-111111111111" for call in calls)


@pytest.mark.asyncio
async def test_provider_limit_stops_immediately_and_keeps_green_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        raise RuntimeError("PAYMENT_REQUIRED")

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        assert action.name == "build"
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(system="s", task="t", execute=execute, max_steps=120)

    assert calls["n"] == 1
    assert res.done is True
    assert res.stop_reason == "provider_stopped_green"


@pytest.mark.asyncio
async def test_native_build_allows_injected_messages_url_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "ok"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_headers={"Authorization": "Bearer runner-token", "X-Trace": "abc"},
        max_steps=5,
    )

    assert res.done is True
    assert all(
        call["url"] == "http://gateway.internal/v1/project-cell/messages" for call in calls
    )
    assert all(
        call["headers"] == {"Authorization": "Bearer runner-token", "X-Trace": "abc"}
        for call in calls
    )


@pytest.mark.asyncio
async def test_call_messages_auth_factory_wires_exact_runner_metadata_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    runner_message_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    async def fake_sleep(seconds: float) -> None:
        _ = seconds

    class FakeClient:
        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            **kwargs: Any,
        ) -> httpx.Response:
            calls.append(
                {
                    "url": url,
                    "json": dict(json),
                    "metadata": dict(json["metadata"]),
                    "timeout": kwargs["timeout"],
                    "headers": (
                        dict(kwargs["headers"]) if kwargs.get("headers") is not None else None
                    ),
                }
            )
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"content": [], "stop_reason": "end_turn"},
            )

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def auth_factory(attempt: int) -> NativeMessagesAttemptAuth:
        assert attempt == 0
        return NativeMessagesAttemptAuth(
            message_id=runner_message_id,
            project_id="22222222-2222-2222-2222-222222222222",
            run_id="33333333-3333-3333-3333-333333333333",
            session_id="44444444-4444-4444-4444-444444444444",
            workspace_id="55555555-5555-5555-5555-555555555555",
            fencing_epoch=7,
            cancel_epoch=2,
            headers={"Authorization": "Bearer runner-0", "X-Trace": "trace-0"},
        )

    response = await agent_native._call_messages(
        FakeClient(),
        "http://gateway.internal/v1/project-cell/messages",
        [{"role": "user", "content": "hi"}],
        "system",
        user_id=None,
        project_id=None,
        run_id=None,
        message_id=None,
        headers={"Authorization": "Bearer stale"},
        auth_factory=auth_factory,
    )

    assert response["stop_reason"] == "end_turn"
    assert calls[0]["url"] == "http://gateway.internal/v1/project-cell/messages"
    assert calls[0]["headers"] == {"Authorization": "Bearer runner-0", "X-Trace": "trace-0"}
    assert calls[0]["metadata"] == {
        "user_id": None,
        "project_id": "22222222-2222-2222-2222-222222222222",
        "run_id": "33333333-3333-3333-3333-333333333333",
        "session_id": "44444444-4444-4444-4444-444444444444",
        "workspace_id": "55555555-5555-5555-5555-555555555555",
        "fencing_epoch": 7,
        "cancel_epoch": 2,
        "message_id": runner_message_id,
        "free": False,
        "stage": "native_agent",
        "retry_count": 0,
    }


@pytest.mark.asyncio
async def test_call_messages_retry_gets_fresh_token_from_auth_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []
    factory_calls: list[int] = []
    runner_message_ids = [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    class FakeClient:
        def __init__(self) -> None:
            self.count = 0

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            **kwargs: Any,
        ) -> httpx.Response:
            calls.append(
                {
                    "url": url,
                    "metadata": dict(json["metadata"]),
                    "headers": (
                        dict(kwargs["headers"]) if kwargs.get("headers") is not None else None
                    ),
                }
            )
            self.count += 1
            if self.count == 1:
                return httpx.Response(429, request=httpx.Request("POST", url), text="rate_limit")
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"content": [], "stop_reason": "end_turn"},
            )

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def auth_factory(attempt: int) -> NativeMessagesAttemptAuth:
        factory_calls.append(attempt)
        return NativeMessagesAttemptAuth(
            message_id=runner_message_ids[attempt],
            project_id="22222222-2222-2222-2222-222222222222",
            run_id="33333333-3333-3333-3333-333333333333",
            session_id="44444444-4444-4444-4444-444444444444",
            workspace_id="55555555-5555-5555-5555-555555555555",
            fencing_epoch=7,
            cancel_epoch=attempt,
            headers={"Authorization": f"Bearer runner-{attempt}"},
        )

    response = await agent_native._call_messages(
        FakeClient(),
        "http://gateway.internal/v1/project-cell/messages",
        [{"role": "user", "content": "hi"}],
        "system",
        auth_factory=auth_factory,
    )

    assert response["stop_reason"] == "end_turn"
    assert factory_calls == [0, 1]
    assert [call["headers"] for call in calls] == [
        {"Authorization": "Bearer runner-0"},
        {"Authorization": "Bearer runner-1"},
    ]
    assert [call["metadata"]["message_id"] for call in calls] == runner_message_ids
    assert [call["metadata"]["cancel_epoch"] for call in calls] == [0, 1]
    assert sleeps == [6.0]


@pytest.mark.asyncio
async def test_native_build_forwards_messages_auth_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def auth_factory(attempt: int) -> NativeMessagesAttemptAuth:
        return NativeMessagesAttemptAuth(
            message_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            project_id="22222222-2222-2222-2222-222222222222",
            run_id="33333333-3333-3333-3333-333333333333",
            session_id="44444444-4444-4444-4444-444444444444",
            workspace_id="55555555-5555-5555-5555-555555555555",
            fencing_epoch=7,
            cancel_epoch=0,
            headers={"Authorization": f"Bearer runner-{attempt}"},
        )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        assert url == "http://gateway.internal/v1/project-cell/messages"
        assert kwargs["auth_factory"] is auth_factory
        return _turn(("done", {"summary": "ok"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_auth_factory=auth_factory,
        max_steps=1,
    )

    assert res.done is True


@pytest.mark.asyncio
async def test_runner_attempt_auth_contract_matches_gateway_validation_and_replay() -> None:
    (
        hs256_jwt_signer,
        project_cell_jwt_messages_auth,
        trusted_runner_identity,
        gateway_settings,
        runner_replay_error,
        gateway_runner_auth,
    ) = _load_cross_package_runner_auth()
    fixed_now = 1_726_000_000
    runner_message_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    identity = trusted_runner_identity(
        project_id=UUID("22222222-2222-2222-2222-222222222222"),
        run_id=UUID("33333333-3333-3333-3333-333333333333"),
        session_id=UUID("44444444-4444-4444-4444-444444444444"),
        workspace_id=UUID("55555555-5555-5555-5555-555555555555"),
        fencing_epoch=7,
        cancel_epoch=2,
    )
    auth = project_cell_jwt_messages_auth(
        signer=hs256_jwt_signer("runner-secret"),
        issuer="omnia-agent-runner",
        audience="omnia-project-cell-runner",
        ttl_seconds=120,
        clock=lambda: fixed_now,
        jti_factory=lambda: UUID(runner_message_id),
        extra_headers={"X-Trace": "trace-0"},
    )
    calls: list[dict[str, Any]] = []

    class FakeClient:
        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            **kwargs: Any,
        ) -> httpx.Response:
            calls.append(
                {
                    "url": url,
                    "metadata": dict(json["metadata"]),
                    "headers": dict(kwargs["headers"]),
                }
            )
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"content": [], "stop_reason": "end_turn"},
            )

    response = await agent_native._call_messages(
        FakeClient(),
        "http://gateway.internal/v1/project-cell/messages",
        [{"role": "user", "content": "hi"}],
        "system",
        stage="verification",
        headers={"Authorization": "Bearer stale"},
        auth_factory=auth.auth_factory(identity),
    )

    assert response["stop_reason"] == "end_turn"
    assert calls[0]["url"] == "http://gateway.internal/v1/project-cell/messages"
    assert calls[0]["headers"]["X-Trace"] == "trace-0"
    assert calls[0]["headers"]["Authorization"] != "Bearer stale"
    assert calls[0]["metadata"]["message_id"] == runner_message_id
    assert calls[0]["metadata"]["retry_count"] == 0
    assert calls[0]["metadata"]["stage"] == "verification"

    claims = gateway_runner_auth.verify_runner_bearer_header(
        calls[0]["headers"]["Authorization"],
        settings=gateway_settings(
            runner_auth_secret="runner-secret",
            runner_auth_issuer="omnia-agent-runner",
            runner_auth_audience="omnia-project-cell-runner",
            runner_auth_max_ttl_seconds=120,
        ),
        now=fixed_now,
    )

    assert claims.jti == runner_message_id
    validated = gateway_runner_auth.validate_runner_metadata(calls[0]["metadata"], claims)
    assert validated["project_id"] == str(identity.project_id)
    assert validated["run_id"] == str(identity.run_id)
    assert validated["session_id"] == str(identity.session_id)
    assert validated["workspace_id"] == str(identity.workspace_id)
    assert validated["fencing_epoch"] == identity.fencing_epoch
    assert validated["cancel_epoch"] == identity.cancel_epoch

    class FakeRedis:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.results = [True, False]

        async def set(
            self,
            name: str,
            value: bytes,
            *,
            ex: int | None = None,
            nx: bool = False,
        ) -> bool:
            self.calls.append({"name": name, "value": value, "ex": ex, "nx": nx})
            return self.results.pop(0)

    fake_redis = FakeRedis()
    await gateway_runner_auth.consume_runner_jti(claims, now=fixed_now, redis_client=fake_redis)
    with pytest.raises(runner_replay_error):
        await gateway_runner_auth.consume_runner_jti(
            claims,
            now=fixed_now,
            redis_client=fake_redis,
        )
    assert fake_redis.calls[0]["name"].endswith(runner_message_id)
    assert fake_redis.calls[0]["nx"] is True
    assert fake_redis.calls[0]["ex"] == 119


@pytest.mark.asyncio
async def test_native_build_defaults_to_legacy_messages_endpoint_without_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return _turn(("done", {"summary": "ok"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=1,
    )

    assert res.done is True
    assert calls[0]["url"] == "http://localhost:8001/v1/messages"
    assert calls[0]["headers"] is None


def test_native_agent_keeps_design_guidance_without_visual_judge() -> None:
    """Design and root-cause guidance do not require a screenshot judge."""
    from omnia_api.services import agent_builder as B

    names = [t["name"] for t in agent_native._TOOLS]
    assert "see" not in names
    assert "see" not in B._KNOWN_ACTIONS

    sysp = agent_native.native_system_prompt("STACK GUIDE", None)
    assert "ВКУС В ДИЗАЙНЕ" in sysp, "design guidance must remain present"
    assert "root-cause" in sysp, "think-before-fix (fewer-bugs) rule must be present"
    assert "`see`" not in sysp


def test_native_agent_can_generate_media() -> None:
    """generate_media (flux image + Kling video, same key) must be ADVERTISED as a
    native tool AND routed by the executor, and the preamble must teach WHEN/HOW to
    use video (scroll-driven hero / 3D fly-through). A dropped tool or missing route
    means the agent can never build the cinematic-video sites the owner asked for."""
    from omnia_api.services import agent_builder as B

    names = [t["name"] for t in agent_native._TOOLS]
    assert "generate_media" in names, "native agent must offer the generate_media tool"
    assert "generate_media" in B._KNOWN_ACTIONS, "executor must route generate_media"

    # The tool schema must expose kind + prompt (required) so the model can pick
    # image vs video — a schema that dropped `kind` would silently force images.
    media = next(t for t in agent_native._TOOLS if t["name"] == "generate_media")
    props = media["input_schema"]["properties"]
    assert "kind" in props and "prompt" in props
    assert media["input_schema"]["required"] == ["kind", "prompt"]
    # Keyframe interpolation (Flux first+last → Kling) is the signature move — the
    # schema MUST offer first_frame/last_frame or the model can't request it.
    assert "first_frame" in props and "last_frame" in props

    # The preamble must carry the video design pattern (scroll-scrub / bg loop) +
    # the keyframe recipe + hover microinteractions, else the model has the tool
    # but no idea when/how to reach for a clip or to make the UI feel alive.
    assert "МЕДИА" in agent_native._NATIVE_PREAMBLE
    assert "video" in agent_native._NATIVE_PREAMBLE.lower()
    assert "КЕЙФРЕЙМ" in agent_native._NATIVE_PREAMBLE  # first+last frame recipe
    assert "МИКРО-ВЗАИМОДЕЙСТВИЯ" in agent_native._NATIVE_PREAMBLE  # hover rules
    # From a PLAIN prompt the agent must reason about the MODEL CHAIN itself
    # (Flux frames → Kling motion → scroll embed) — not require the user to name
    # models. Drop this and a normal request never triggers the cinematic combo.
    assert "ОРКЕСТРАЦИЯ МОДЕЛЕЙ" in agent_native._NATIVE_PREAMBLE
    # Scroll-scrub jank is a real defect (measured 2026-07-17) — the preamble must
    # carry the 60fps smoothness contract (rAF-only currentTime, GPU compositing).
    assert "ПЛАВНОСТЬ" in agent_native._NATIVE_PREAMBLE
    assert "ОРКЕСТРАЦИЯ МОДЕЛЕЙ" not in agent_native._MAX_NATIVE_PREAMBLE
    assert "МЕДИА" not in agent_native._MAX_NATIVE_PREAMBLE
    assert "ЖИВЫЕ МИКРО-ВЗАИМОДЕЙСТВИЯ" not in agent_native._MAX_NATIVE_PREAMBLE


def test_generate_media_returns_url_in_model_visible_field() -> None:
    """The whole feature dies if the agent can't SEE the generated URL: the native
    loop feeds a tool result back via `content`/`detail`/`error` (never the bare
    `url` key), so generate_media MUST echo the URL inside `content`. This guards
    the review-2026-07-17 critical: url-only → model gets "ok" → no <img>/<video>."""
    import asyncio

    from omnia_api.services import agent_media

    async def _fake_gen(project_id: str, prompt: str) -> str:
        return "http://minio.local/omnia-images/p/deadbeef.png"

    # Stub the flux call so this stays a pure unit test (no gateway/MinIO).
    orig = agent_media.image_resolver.generate_and_store_image
    agent_media.image_resolver.generate_and_store_image = _fake_gen  # type: ignore[assignment]
    try:
        res = asyncio.run(agent_media.generate_media("p", kind="image", prompt="cinematic hero"))
    finally:
        agent_media.image_resolver.generate_and_store_image = orig  # type: ignore[assignment]

    assert res["ok"] is True
    assert res["url"] == "http://minio.local/omnia-images/p/deadbeef.png"
    # The URL must live in a field the model actually reads back (content), not
    # only in `url` which _obs_to_tool_result ignores.
    assert res["url"] in str(res["content"])
    body = agent_native._obs_to_tool_result("tu_1", res)["content"]
    assert res["url"] in body  # end-to-end: model truly receives the URL
