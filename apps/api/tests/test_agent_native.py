"""Tests for the native tool-use build loop (services/agent_native).

Covers: `_module_not_found_hint` (anti-hallucination recovery), bounded generic
builds, and the MAX lifecycle which keeps repairing until its factual completion
contract is green or a durable spend/provider/cancellation guard stops it.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from omnia_api.routers import messages
from omnia_api.services import agent_native
from omnia_api.services.agent_native import _module_not_found_hint
from omnia_api.services.max_generation_contract import (
    MAX_REQUIRED_POST_SEE_SKILL,
    MAX_REQUIRED_PREWRITE_SKILLS,
)


@pytest.mark.asyncio
async def test_max_visual_qa_recovers_after_transient_preview_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import agent_vision, max_functional_gate
    from omnia_api.services.functional_gate import Check, summarize

    project_id = uuid4()
    session_calls = 0
    sleeps: list[int] = []

    async def fake_session(received_project_id: Any) -> dict[str, str]:
        nonlocal session_calls
        assert received_project_id == project_id
        session_calls += 1
        if session_calls == 1:
            raise httpx.ConnectError("preview control plane warming")
        return {"bootstrap_url": "https://preview.example/session?signature=secret"}

    async def fake_see(received_project_id: Any, **kwargs: Any) -> dict[str, Any]:
        assert received_project_id == project_id
        assert kwargs["path"] == "/"
        assert kwargs["product_kind"] == "max_miniapp"
        return {
            "ok": True,
            "verdict": "beautiful",
            "score": 9,
            "needs_fix": False,
        }

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    async def fake_functional(_url: str, *, require_persistence: bool) -> Any:
        assert require_persistence is False
        return summarize([Check("max_signed_functional", True, "green")])

    monkeypatch.setattr(
        messages.orchestrator_client,
        "create_max_preview_session",
        fake_session,
    )
    monkeypatch.setattr(agent_vision, "see_page", fake_see)
    monkeypatch.setattr(max_functional_gate, "run_max_functional_gate", fake_functional)
    monkeypatch.setattr(messages.asyncio, "sleep", fake_sleep)

    result = await messages._run_max_visual_qa(
        project_id,
        path="/",
        prompt_context="restaurant app",
    )

    assert result["ok"] is True
    assert result["score"] == 9
    assert session_calls == 2
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_max_visual_qa_does_not_retry_real_browser_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import agent_vision

    session_calls = 0
    see_calls = 0

    async def fake_session(_project_id: Any) -> dict[str, str]:
        nonlocal session_calls
        session_calls += 1
        return {"bootstrap_url": "https://preview.example/session?signature=secret"}

    async def fake_see(_project_id: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal see_calls
        see_calls += 1
        return {
            "ok": False,
            "verdict": "beautiful",
            "score": 9,
            "needs_fix": False,
            "detail": "BROWSER SIGNALS:\n- GET /api/menu 500",
        }

    monkeypatch.setattr(
        messages.orchestrator_client,
        "create_max_preview_session",
        fake_session,
    )
    monkeypatch.setattr(agent_vision, "see_page", fake_see)

    result = await messages._run_max_visual_qa(
        uuid4(),
        path="/",
        prompt_context="restaurant app",
    )

    assert result["ok"] is False
    assert "BROWSER SIGNALS" in result["detail"]
    assert session_calls == 1
    assert see_calls == 1


def test_generic_native_agent_and_autoheal_keep_primary_model() -> None:
    from omnia_api.services import autoheal

    assert agent_native._MODEL == "gemini-3.1-pro-preview-customtools"
    assert autoheal._HEAL_MODEL == "gemini-3.1-pro-preview-customtools"


def test_max_native_prompt_exposes_complete_safe_product_toolset() -> None:
    prompt = agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT\nBuild the app",
        "MAX capability catalog: call read_skill(`ui-ux-pro-max`)",
        stable_max_loop=True,
    )

    assert "OMNIA MAX APP ENGINEER" in prompt
    assert "АРТ-ДИРЕКЦИЯ ПРИНАДЛЕЖИТ ТЕБЕ" in prompt
    assert "ProductApp.tsx" in prompt
    assert "защищённое ядро" in prompt
    assert "MAX PLATFORM CORE CONTRACT" in prompt
    assert "ОРКЕСТРАЦИЯ МОДЕЛЕЙ" not in prompt
    assert "Sonnet" not in prompt
    assert "Gemini" not in prompt
    assert "read_skill" in prompt
    assert "Ни один навык не является обязательной" in prompt
    assert "MAX capability catalog" not in prompt
    assert "signed MAX preview session" in prompt
    assert "build" in prompt
    names = {tool["name"] for tool in agent_native._TOOLS_CACHED}
    assert {"read_file", "write_file", "build", "done"} <= names
    assert "read_skill" not in names
    assert agent_native._TOOLS_CACHED[-1]["cache_control"] == agent_native._CACHE
    stable_names = {tool["name"] for tool in agent_native._STABLE_MAX_TOOLS_CACHED}
    assert {
        "plan_task",
        "update_plan",
        "list_dir",
        "read_file",
        "grep",
        "docs",
        "write_file",
        "edit_file",
        "build",
        "read_logs",
        "runtime_check",
        "see",
        "read_skill",
        "discover_capabilities",
        "call_capability",
        "bash",
        "generate_media",
        "done",
    } == stable_names
    assert not ({"probe", "verify_isolation"} & stable_names)
    assert agent_native._MAX_TOKENS == 32_768
    assert "именно во вкладке MAX" in prompt
    assert "не Telegram/VK Mini App" in prompt


def test_max_edit_prompt_requires_runtime_proof_without_visual_ceremony() -> None:
    prompt = agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT\nPreserve the app",
        stable_max_loop=True,
        stable_max_edit=True,
    )

    assert "обязательно проверь итог через see" not in prompt
    assert "build/runtime_check" in prompt


def test_generic_native_prompt_stays_unchanged_outside_max() -> None:
    prompt = agent_native.native_system_prompt("Build the web app")

    assert "автономный инженер" in prompt
    assert "ОРКЕСТРАЦИЯ МОДЕЛЕЙ" in prompt


def test_server_working_memory_is_ephemeral_and_preserves_tool_result_order() -> None:
    convo = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r1"}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "r1", "content": "read ok"}
            ],
        },
    ]

    enriched = agent_native._with_working_memory(convo, "SERVER NOTE")

    assert enriched is not convo
    assert "SERVER NOTE" not in str(convo)
    assert enriched[-1]["content"][0]["type"] == "tool_result"
    assert "SERVER NOTE" in enriched[-1]["content"][0]["content"]


def test_legacy_reference_flags_do_not_change_the_stable_prompt() -> None:
    baseline = agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT\nBuild the app")
    prompt = agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT\nBuild the app",
        reference_max_loop=True,
    )

    assert prompt == baseline
    assert "Google" not in prompt

    edit_prompt = agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT\nPreserve the app",
        reference_max_loop=True,
        reference_max_edit=True,
    )
    assert edit_prompt == agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT\nPreserve the app"
    )


def test_first_max_build_starts_green_and_runs_bounded_sonnet_loop() -> None:
    """MAX starts from a working shell, then Sonnet rewrites product files."""
    source = inspect.getsource(messages._process_prompt)

    assert 'stop_reason="deterministic_template"' not in source
    assert "_publishable_agent_files" in source
    assert "must_restore_previous=_must_restore_previous" in source
    assert "agent_native.run_native_build" in source
    assert (
        'project_template == "max_miniapp"\n                '
        "or agent_builder.is_agentic_enabled"
    ) in source
    assert (
        'project_template == "max_miniapp" or get_settings().use_native_agent'
    ) in source
    assert "build_max_product_contract" in source
    assert "max_completion_gap" in source
    assert "_max_completion_check" in source
    assert "reference_max_loop=False" in source
    assert "max_steps=_agent_steps" in source
    assert "model=(" in source
    assert "MAX_STUDIO_LLM_MODEL" in source
    assert 'stable_max_loop=project_template == "max_miniapp"' in source
    assert "enforce_max_skill_lifecycle=False" in source
    assert 'project_template == "max_miniapp" and not _max_has_generated_snapshot' in source
    assert "progress_context=" in source
    assert "_max_design_dna = None" in source
    assert "Определяю арт-дирекцию и продуктовые возможности" not in source
    assert "load_stack_skill_index(_orch_name)" in source
    assert 'if action.name == "read_skill"' in source
    assert 'if action.name == "plan_task"' in source
    assert 'if action.name == "update_plan"' in source
    assert 'if action.name in {"discover_capabilities", "call_capability"}' in source
    assert "await save_generation_agent_state(run_id, _agent_state)" in source
    assert "McpBroker()" in source
    assert "action.path in MAX_MODEL_LOCKED_FILES" in source
    assert "max_model_path_rejection(action.path)" in source
    assert "max_model_write_rejection(action.path, _candidate)" in source
    assert "_agent_steps = 120" in source
    assert "render_max_starter_files" not in source
    assert "Подготавливаю защищённое ядро MAX" not in source
    assert "_capture_max_runtime_checkpoint" in source
    assert "_restore_max_runtime_checkpoint" in source
    assert '"autonomous_recovery"' not in source
    assert "_seg <" not in source
    assert "_first_max_without_product" in source
    assert "func.length(func.trim(Snapshot.prompt_text)) > 0" in source
    assert "_preserve_verified_max_progress" in source
    assert "and not _preserve_green_max_progress" in source
    assert "if path not in MAX_MODEL_LOCKED_FILES" in source
    assert "max_model_path_rejection(action.path)" in source
    assert "_max_runtime_probe_is_green" in source
    assert "pg_advisory_xact_lock" in source
    assert "project.current_snapshot_id != current_snapshot_id" in source
    assert "Direct DB access is forbidden in MAX product files." in source
    assert "Environment and secret files are not agent-readable." in source
    assert "max_model_write_rejection" in source
    assert 'and not _agent_res.stop_reason.startswith("spend_budget")' in source
    assert "max_demo_data_rejection" not in source
    assert "_run_max_visual_qa" in source
    assert "_recover_max_resume_prompt" in source
    assert "_merge_max_product_brief" in source
    assert "max_completion_gap" in source
    assert "src/components/product/ProductApp.tsx" in source
    assert "normalize_max_globals_css" in source
    assert "await asyncio.sleep(2)" in source
    assert 'max_runtime=project_template == "max_miniapp"' in source
    assert 'product_kind="max_miniapp"' in inspect.getsource(messages._run_max_visual_qa)
    assert "EXISTING MAX ART DIRECTION" not in source
    assert "enforce_max_skill_lifecycle" in source
    assert "run_max_hydration_check(project_id)" in source
    assert "_max_terminal_failure(" in source


def test_failed_max_terminal_body_marks_a_claimed_success_incomplete() -> None:
    body = messages._failed_max_terminal_body(
        "Готово — приложение собрано.", "экран не смонтирован"
    )

    assert body.startswith("[Сборка MAX не завершена: экран не смонтирован]")
    assert body.endswith("Готово — приложение собрано.")


def test_max_terminal_failure_rejects_missing_snapshot_and_failed_hydration() -> None:
    missing = messages._max_terminal_failure(
        project_template="max_miniapp",
        has_snapshot_files=False,
        verification_failed=False,
    )
    failed = messages._max_terminal_failure(
        project_template="max_miniapp",
        has_snapshot_files=True,
        verification_failed=True,
    )

    assert missing is not None and missing[0] == "max_snapshot_missing"
    assert failed is not None and failed[0] == "final_verification_failed"
    assert (
        messages._max_terminal_failure(
            project_template="max_miniapp",
            has_snapshot_files=True,
            verification_failed=False,
        )
        is None
    )
    assert (
        messages._max_terminal_failure(
            project_template="nextjs_entities",
            has_snapshot_files=False,
            verification_failed=False,
        )
        is None
    )


def test_fresh_max_reference_gate_rejects_css_only_or_managed_empty_entry() -> None:
    evidence: dict[str, int] = {}

    assert messages._reference_max_completion_gap(
        {"src/app/globals.css": "body { color: black; }"},
        evidence,
        require_product_entry=True,
    )
    assert messages._reference_max_completion_gap(
        {
            "src/components/product/ProductApp.tsx": (
                "export default function ProductApp() { return "
                '<main data-max-product-canvas="empty" />; }'
            )
        },
        evidence,
        require_product_entry=True,
    )
    entry = "export default function ProductApp() { return <main>Рабочий продукт</main>; }"
    assert (
        messages._reference_max_completion_gap(
            {"src/components/product/ProductApp.tsx": entry},
            evidence,
            require_product_entry=True,
        )
        is None
    )


def test_fresh_max_reference_gate_does_not_judge_css_architecture() -> None:
    entry = (
        'export default function ProductApp() { return <main className="app-shell">'
        '<header className="app-header"><h1 className="hero-title">Fit</h1></header>'
        '<button className="primary-action">Старт</button>' + ("product " * 60) + "</main>; }"
    )
    starter_css = ".max-shell { padding: 20px; }\n" + ("/* starter */\n" * 40)

    assert (
        messages._reference_max_completion_gap(
            {
                "src/components/product/ProductApp.tsx": entry,
                "src/app/globals.css": starter_css,
            },
            {"runtime_check_after_write": 1, "see_after_write": 1},
            require_product_entry=True,
        )
        is None
    )


def test_fresh_max_reference_gate_rejects_legacy_max_ui_but_edit_allows_it() -> None:
    legacy_component = (
        'import { Button } from "@maxhub/max-ui"; '
        "export default function ProductApp(){return <Button>Start</Button>}"
    )

    assert "historical snapshots" in str(
        messages._reference_max_completion_gap(
            {"src/components/product/ProductApp.tsx": legacy_component},
            {},
            require_product_entry=True,
        )
    )
    assert (
        messages._reference_max_completion_gap(
            {"src/components/product/LegacyCard.tsx": legacy_component},
            {},
            require_product_entry=False,
        )
        is None
    )
    assert messages._fresh_max_product_write_rejection(
        legacy_component,
        has_generated_snapshot=False,
    )
    assert (
        messages._fresh_max_product_write_rejection(
            legacy_component,
            has_generated_snapshot=True,
        )
        is None
    )


def test_fresh_max_reference_gate_does_not_require_runtime_ceremony() -> None:
    entry = (
        'export default function ProductApp() { return <main className="app-shell">'
        '<header className="app-header"><h1 className="hero-title">Fit</h1></header>'
        '<button className="primary-action">Старт</button>' + ("product " * 60) + "</main>; }"
    )
    deceptive_css = (
        "/* .app-shell .app-header .hero-title .primary-action */\n"
        ".unrelated { padding: 20px; }\n" + ("/* enough bytes but no product selectors */\n" * 20)
    )

    assert (
        messages._reference_max_completion_gap(
            {
                "src/components/product/ProductApp.tsx": entry,
                "src/app/globals.css": deceptive_css,
            },
            {},
            require_product_entry=True,
        )
        is None
    )


def test_fresh_max_reference_gate_finishes_after_runtime_without_visual_ceremony() -> None:
    entry = (
        'export default function ProductApp() { return <main className="app-shell">'
        + ("product " * 60)
        + "</main>; }"
    )
    styles = ".app-shell { min-height: 100dvh; }\n" + ("/* visual */\n" * 40)

    gap = messages._reference_max_completion_gap(
        {
            "src/components/product/ProductApp.tsx": entry,
            "src/app/globals.css": styles,
        },
        {"runtime_check_after_write": 1},
        require_product_entry=True,
    )

    assert gap is None


def test_max_edit_reference_gate_finishes_after_runtime_and_visual_proof() -> None:
    gap = messages._reference_max_completion_gap(
        {"src/app/globals.css": "body { color: black; }"},
        {"runtime_check_after_write": 1, "see_after_write": 1},
        require_product_entry=False,
    )

    assert gap is None


def test_max_edit_keeps_original_product_brief_visible() -> None:
    merged = messages._merge_max_product_brief(
        "Ресторан: iiko, ЮKassa, каталог и заказы.",
        "Сделай кнопки теплее.",
    )

    assert "ИСХОДНЫЙ БРИФ ПРОДУКТА" in merged
    assert "iiko, ЮKassa" in merged
    assert "ТЕКУЩАЯ ПРАВКА" in merged
    assert "кнопки теплее" in merged
    assert messages._merge_max_product_brief("тот же текст", "тот же текст") == "тот же текст"


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        ({"ok": True, "status_code": 200}, True),
        ({"ok": True, "status_code": 307}, True),
        ({"ok": True, "status_code": 404}, False),
        ({"ok": False, "status_code": 500}, False),
        ({"ok": True, "status_code": None}, False),
    ],
)
def test_max_runtime_completion_requires_a_real_route(
    probe: dict[str, object], expected: bool
) -> None:
    assert messages._max_runtime_probe_is_green(probe) is expected


def test_failed_max_resume_recovers_the_original_brief() -> None:
    from omnia_api.routers.messages import _recover_max_resume_prompt

    assert (
        _recover_max_resume_prompt(
            ["продолжи", "Продолжай сборку", "Собери фитнес-тренера с ИИ и статистикой"]
        )
        == "Собери фитнес-тренера с ИИ и статистикой"
    )
    assert _recover_max_resume_prompt(["продолжи", "доделай"]) is None


def test_rolled_back_max_generation_is_never_reported_as_done() -> None:
    from omnia_api.routers.messages import _agent_result_message
    from omnia_api.services.agent_builder import AgentResult

    result = AgentResult(
        done=False,
        summary="Первая генерация не завершена; среда восстановлена.",
        files={},
        steps=30,
        stop_reason="first_build_rolled_back",
    )

    assert _agent_result_message(result, is_edit=False).startswith("Первая генерация не завершена")


def test_seeded_max_files_are_committed_with_agent_customisations() -> None:
    from omnia_api.routers.messages import _merge_seeded_agent_files

    starter = {"src/app/page.tsx": "starter", "src/app/globals.css": "safe css"}
    generated = {"src/app/page.tsx": "google agent result", "src/components/Profile.tsx": "ui"}

    merged = _merge_seeded_agent_files(starter, generated)

    assert merged == {
        "src/app/page.tsx": "google agent result",
        "src/app/globals.css": "safe css",
        "src/components/Profile.tsx": "ui",
    }
    assert starter["src/app/page.tsx"] == "starter"


def test_first_max_runtime_restore_uses_checkpoint_not_generated_core() -> None:
    from omnia_api.routers.messages import (
        _max_runtime_checkpoint_path,
        _max_runtime_restore_patch,
    )

    assert _max_runtime_checkpoint_path("package.json")
    assert _max_runtime_checkpoint_path("src/app/globals.css")
    assert _max_runtime_checkpoint_path("src/components/product/ProductApp.tsx")
    assert not _max_runtime_checkpoint_path("src/app/page.tsx")
    assert not _max_runtime_checkpoint_path(".env")

    checkpoint = {
        "package.json": '{"scripts":{"build":"next build"}}',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'",
        "src/app/globals.css": "body { color: black; }",
        "src/components/product/ProductApp.tsx": "export default function ProductApp() {}",
    }

    patch = _max_runtime_restore_patch(
        checkpoint,
        [
            *checkpoint,
            "src/components/product/NewScreen.tsx",
            "src/lib/product/state.ts",
            "src/app/page.tsx",
        ],
    )

    assert patch == {
        "src/components/product/NewScreen.tsx": "",
        "src/lib/product/state.ts": "",
        **checkpoint,
    }
    assert "src/app/page.tsx" not in patch


@pytest.mark.asyncio
async def test_failed_first_max_cleanup_restores_rendered_starter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    snapshot_id = uuid4()
    project = SimpleNamespace(
        id=project_id,
        slug="fresh-max",
        name="Новый MAX",
        template="max_miniapp",
        current_snapshot_id=snapshot_id,
        runtime_sync_required=True,
        runtime_sync_paths=["src/app/globals.css"],
    )
    snapshot = SimpleNamespace(commit_sha="empty-initial")

    class FakeSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def get(self, model: Any, key: Any) -> Any:
            if model is messages.Project and key == project_id:
                return project
            if model is messages.Snapshot and key == snapshot_id:
                return snapshot
            return None

        async def commit(self) -> None:
            return None

    class SessionContext:
        async def __aenter__(self) -> FakeSession:
            return FakeSession()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(messages, "get_engine", lambda: object())
    monkeypatch.setattr(
        messages,
        "async_sessionmaker",
        lambda *_args, **_kwargs: lambda: SessionContext(),
    )
    monkeypatch.setattr(messages.repo_svc, "read_files", lambda *_args: {})
    applied: dict[str, str] = {}

    async def hot_reload(_project_id: Any, _slug: str, patch: dict[str, str]) -> None:
        applied.update(patch)

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload_exact", hot_reload)

    await messages._resync_cancelled_runtime(
        project_id,
        {
            "src/app/globals.css",
            "src/components/product/ProductApp.tsx",
        },
    )

    assert '@import "tailwindcss"' in applied["src/app/globals.css"]
    assert 'data-max-product-canvas="empty"' in applied["src/components/product/ProductApp.tsx"]


def test_unsafe_agent_stop_never_exposes_partial_files_for_publication() -> None:
    from omnia_api.routers.messages import _publishable_agent_files

    assert (
        _publishable_agent_files(
            {"src/app/page.tsx": "safe baseline"},
            {
                "src/app/page.tsx": "partial rewrite",
                "src/components/Unfinished.tsx": "red file",
            },
            must_restore_previous=True,
        )
        == {}
    )

    assert _publishable_agent_files(
        {"src/app/page.tsx": "safe baseline"},
        {"src/app/page.tsx": "complete rewrite"},
        must_restore_previous=False,
    ) == {"src/app/page.tsx": "complete rewrite"}


def test_green_max_edit_is_preserved_only_as_verified_continuation() -> None:
    from omnia_api.routers.messages import _preserve_verified_max_progress

    files = {"src/components/product/ProductApp.tsx": "green candidate"}
    assert _preserve_verified_max_progress(
        project_template="max_miniapp",
        is_edit=True,
        stop_reason="max_steps_green",
        generated_files=files,
    )
    assert not _preserve_verified_max_progress(
        project_template="max_miniapp",
        is_edit=False,
        stop_reason="max_steps_green",
        generated_files=files,
    )
    assert not _preserve_verified_max_progress(
        project_template="max_miniapp",
        is_edit=True,
        stop_reason="max_steps_red",
        generated_files=files,
    )
    assert not _preserve_verified_max_progress(
        project_template="max_miniapp",
        is_edit=True,
        stop_reason="max_steps_green",
        generated_files={},
    )


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
async def test_stable_max_loop_forces_a_first_write_after_bounded_exploration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[set[str]] = []
    executed_reads = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        names = {tool["name"] for tool in kwargs["tools"]}
        calls.append(names)
        if "read_file" in names and len(calls) <= agent_native._STABLE_MAX_FIRST_WRITE_AT:
            return _turn(("read_file", {"path": "src/components/product/ProductApp.tsx"}))
        if names == {"write_file"}:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": (
                            "export default function ProductApp(){return <main>Готово</main>}"
                        ),
                    },
                )
            )
        if len(calls) == agent_native._STABLE_MAX_FIRST_WRITE_AT + 2:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads
        if action.name == "read_file":
            executed_reads += 1
            return {"ok": True, "content": "starter"}
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        return {"ok": True, "detail": "clean"}

    result = await agent_native.run_native_build(
        system="MAX starter",
        task="build product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "done"
    assert executed_reads == 1
    assert calls[agent_native._STABLE_MAX_FIRST_WRITE_AT] == {"write_file"}
    assert "src/components/product/ProductApp.tsx" in result.files


@pytest.mark.asyncio
async def test_stable_max_entry_focus_still_allows_required_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_skills: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        names = {tool["name"] for tool in kwargs["tools"]}
        if calls <= agent_native._STABLE_MAX_ENTRY_FOCUS_AT:
            return _turn(("read_file", {"path": agent_native._STABLE_MAX_PRODUCT_ENTRY}))
        if calls == agent_native._STABLE_MAX_ENTRY_FOCUS_AT + 1:
            assert "read_skill" in names
            return _turn(
                *(
                    ("read_skill", {"skill": skill, "reason": "required"})
                    for skill in MAX_REQUIRED_PREWRITE_SKILLS
                )
            )
        if calls == agent_native._STABLE_MAX_ENTRY_FOCUS_AT + 2:
            assert names == {"write_file"}
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": "export default function ProductApp(){return <main>MAX</main>}",
                    },
                )
            )
        if calls == agent_native._STABLE_MAX_ENTRY_FOCUS_AT + 3:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "read_skill":
            executed_skills.append(str(action.args["skill"]))
            return {"ok": True, "content": "loaded"}
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        if action.name == "read_file":
            return {"ok": True, "content": "starter"}
        return {"ok": True, "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        if agent_native._STABLE_MAX_PRODUCT_ENTRY not in files:
            return "missing ProductApp"
        if not all(evidence.get(f"skill:{skill}") for skill in MAX_REQUIRED_PREWRITE_SKILLS):
            return "missing required skill"
        return None if evidence.get("build_after_write") else "missing build"

    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build MAX app",
        execute=execute,
        completion_check=complete,
        enforce_max_skill_lifecycle=True,
        stable_max_loop=True,
        stable_max_product_first=True,
        max_steps=20,
    )

    assert result.done is True
    assert set(executed_skills) == set(MAX_REQUIRED_PREWRITE_SKILLS)
    assert agent_native._STABLE_MAX_PRODUCT_ENTRY in result.files


@pytest.mark.asyncio
async def test_stable_max_caps_bundled_prewrite_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_reads: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _turn(*(("read_file", {"path": f"src/core-{index}.ts"}) for index in range(12)))
        if calls == 2:
            assert {tool["name"] for tool in kwargs["tools"]} == {"write_file"}
            assert "inspected enough" in str(convo[-1])
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": "export default function ProductApp(){return <main>App</main>}",
                    },
                )
            )
        if calls == 3:
            assert {tool["name"] for tool in kwargs["tools"]} == {"build"}
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "read_file":
            executed_reads.append(action.path)
            return {"ok": True, "content": "managed core"}
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert len(executed_reads) == agent_native._STABLE_MAX_PREWRITE_INSPECTION_LIMIT


@pytest.mark.asyncio
async def test_stable_max_first_write_is_enforced_when_provider_reuses_old_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_reads = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls <= agent_native._STABLE_MAX_FIRST_WRITE_AT:
            return _turn(("read_file", {"path": "src/components/product/ProductApp.tsx"}))
        if calls == agent_native._STABLE_MAX_FIRST_WRITE_AT + 1:
            # Simulate a compatible gateway/provider continuing to call a tool
            # advertised earlier in the cached conversation.
            assert {tool["name"] for tool in kwargs["tools"]} == {"write_file"}
            return _turn(("grep", {"pattern": "ProductApp", "path": "src"}))
        if calls == agent_native._STABLE_MAX_FIRST_WRITE_AT + 2:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function ProductApp(){return <main>OK</main>}",
                    },
                )
            )
        if calls == agent_native._STABLE_MAX_FIRST_WRITE_AT + 3:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads
        if action.name == "read_file":
            executed_reads += 1
            return {"ok": True, "content": "existing product"}
        if action.name == "grep":
            raise AssertionError("executor-level first-write gate was bypassed")
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        return {"ok": True, "detail": "clean"}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert executed_reads == 1
    assert "[FOCUSED PRODUCT ENTRY]" in str(result.transcript)


@pytest.mark.asyncio
async def test_stable_max_rejects_support_files_before_product_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_paths: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls <= agent_native._STABLE_MAX_FIRST_WRITE_AT:
            return _turn(("read_file", {"path": "src/lib/omnia/max-config.ts"}))
        if calls == agent_native._STABLE_MAX_FIRST_WRITE_AT + 1:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/types.ts",
                        "content": "export type Item = { id: string }",
                    },
                )
            )
        if calls == agent_native._STABLE_MAX_FIRST_WRITE_AT + 2:
            assert len(convo) == 1
            assert "[FOCUSED PRODUCT ENTRY]" in str(convo[-1])
            path_schema = kwargs["tools"][0]["input_schema"]["properties"]["path"]
            assert path_schema["enum"] == [agent_native._STABLE_MAX_PRODUCT_ENTRY]
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": (
                            "export default function ProductApp(){return <main>Full app</main>}"
                        ),
                    },
                )
            )
        if calls == agent_native._STABLE_MAX_FIRST_WRITE_AT + 3:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name in {"write_file", "edit_file"}:
            executed_paths.append(action.path)
            return {"ok": True, "content": action.args.get("content", "")}
        return {"ok": True, "content": "starter", "detail": "clean"}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build full product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert executed_paths == [agent_native._STABLE_MAX_PRODUCT_ENTRY]
    assert "src/components/product/types.ts" not in result.files


def test_stable_max_preentry_surface_allows_only_product_entry_write() -> None:
    names = {tool["name"] for tool in agent_native._STABLE_MAX_PREENTRY_TOOLS_CACHED}
    assert names == {
        "plan_task",
        "update_plan",
        "list_dir",
        "read_file",
        "grep",
        "docs",
        "read_skill",
        "discover_capabilities",
        "call_capability",
        "bash",
        "write_file",
    }
    assert not ({"edit_file", "build", "done"} & names)

    write_tool = next(
        tool
        for tool in agent_native._STABLE_MAX_PREENTRY_TOOLS_CACHED
        if tool["name"] == "write_file"
    )
    assert write_tool["input_schema"]["properties"]["path"]["enum"] == [
        agent_native._STABLE_MAX_PRODUCT_ENTRY
    ]


@pytest.mark.asyncio
async def test_stable_max_edit_can_change_support_file_without_rewriting_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/StatsCard.tsx",
                        "content": "export default function StatsCard(){return <div>42</div>}",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("done", {"summary": "Готово"})),
        ]
    )
    advertised: list[set[str]] = []
    executed_paths: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({tool["name"] for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "write_file":
            executed_paths.append(action.path)
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="update one existing component",
        execute=execute,
        max_steps=8,
        stable_max_loop=True,
        stable_max_product_first=False,
    )

    assert result.done is True
    assert executed_paths == ["src/components/product/StatsCard.tsx"]
    assert "edit_file" in advertised[0]
    assert agent_native._STABLE_MAX_PRODUCT_ENTRY not in result.files


@pytest.mark.asyncio
async def test_stable_max_skips_repeated_unchanged_reads_and_injects_working_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_reads = 0
    notes: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        notes.append(str(kwargs.get("working_memory") or ""))
        if calls <= 2:
            return _turn(("read_file", {"path": "src/lib/omnia/max-config.ts"}))
        if calls == 3:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/StatsCard.tsx",
                        "content": "export default function StatsCard(){return <div>42</div>}",
                    },
                )
            )
        if calls == 4:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads
        if action.name == "read_file":
            executed_reads += 1
            return {"ok": True, "content": "export const maxConfig = {}"}
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="edit existing product",
        execute=execute,
        max_steps=8,
        stable_max_loop=True,
        stable_max_product_first=False,
        progress_context=lambda: "RECOVERED EXECUTION CHECKPOINT",
    )

    assert result.done is True
    assert executed_reads == 1
    assert "Phase: edit_existing_product" in notes[0]
    assert "Product entry state: existing_snapshot" in notes[0]
    assert any("Already observed unchanged" in note for note in notes)
    assert all("RECOVERED EXECUTION CHECKPOINT" in note for note in notes)


@pytest.mark.asyncio
async def test_stable_max_restores_observation_memory_after_worker_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "src/lib/omnia/max-config.ts"
    calls = 0
    executed_reads = 0
    notes: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        notes.append(str(kwargs.get("working_memory") or ""))
        if calls == 1:
            return _turn(("read_file", {"path": path}))
        if calls == 2:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/StatsCard.tsx",
                        "content": "export default function StatsCard(){return <div>42</div>}",
                    },
                )
            )
        if calls == 3:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads
        if action.name == "read_file":
            executed_reads += 1
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="resume existing product",
        execute=execute,
        max_steps=8,
        stable_max_loop=True,
        stable_max_product_first=False,
        resume_checkpoint={
            "version": 2,
            "system": "MAX runtime",
            "convo": [{"role": "user", "content": "resume existing product"}],
            "provider_turn_index": 7,
            "workspace_revision": 3,
            "source_revisions": {path: 2},
            "observed_revisions": {f"read:{path}": 2},
            "no_write_turns": 4,
        },
    )

    assert result.done is True
    assert executed_reads == 0
    assert "Consecutive turns without source progress: 4" in notes[0]
    assert any("Already observed unchanged" in note for note in notes[1:])


@pytest.mark.asyncio
async def test_stable_max_forces_build_immediately_after_product_entry_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_reads = 0
    executed_writes = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": "export default function ProductApp(){return <main>App</main>}",
                    },
                )
            )
        if calls == 2:
            assert {tool["name"] for tool in kwargs["tools"]} == {"build"}
            # Simulate a cached provider turn trying to rewrite the complete
            # product again. Executor enforcement must reject it.
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            "export default function ProductApp(){return <main>rewrite</main>}"
                        ),
                    },
                )
            )
        if calls == 3:
            assert {tool["name"] for tool in kwargs["tools"]} == {"build"}
            assert "must be compiled" in str(convo[-1])
            return _turn(("build", {}))
        if calls == 4:
            return _turn(("read_file", {"path": "src/lib/omnia/max-config.ts"}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads, executed_writes
        if action.name == "read_file":
            executed_reads += 1
            return {"ok": True, "content": "managed integration"}
        if action.name == "write_file":
            executed_writes += 1
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build full product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert executed_reads == 1
    assert executed_writes == 1
    assert calls == 5


@pytest.mark.asyncio
async def test_stable_max_forces_product_css_after_green_unstyled_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    writes: list[str] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": "export default function ProductApp(){return <main>App</main>}",
                    },
                )
            )
        if calls == 2:
            return _turn(("build", {}))
        if calls == 3:
            assert {tool["name"] for tool in kwargs["tools"]} == {"write_file"}
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            "export default function ProductApp(){return <main>rewrite</main>}"
                        ),
                    },
                )
            )
        if calls == 4:
            assert "visual system is missing" in str(convo[-1])
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/app/globals.css",
                        "content": '@import "tailwindcss";\nbody { color: black; }',
                    },
                )
            )
        assert {tool["name"] for tool in kwargs["tools"]} == {"build"}
        return _turn(("build", {}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "write_file":
            writes.append(action.path)
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def completion(files: dict[str, str], _evidence: dict[str, int]) -> str | None:
        if "src/app/globals.css" not in files:
            return "A fresh product must rewrite src/app/globals.css."
        return None

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build product",
        execute=execute,
        completion_check=completion,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert writes == [agent_native._STABLE_MAX_PRODUCT_ENTRY, "src/app/globals.css"]
    assert calls == 5


@pytest.mark.asyncio
async def test_stable_max_truncated_write_is_retried_as_smaller_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            truncated = _turn(("write_file", {}))
            truncated["stop_reason"] = "max_tokens"
            return truncated
        if calls == 2:
            assert "[OUTPUT LIMIT]" in str(convo[-1])
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function ProductApp(){return <main>OK</main>}",
                    },
                )
            )
        if calls == 3:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "write_file" and not action.path:
            return {"ok": False, "error": "write_file needs path + content"}
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build product",
        execute=execute,
        max_steps=10,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "done"
    assert calls == 4
    assert "src/components/product/ProductApp.tsx" in result.files


@pytest.mark.asyncio
async def test_stable_max_stops_paid_calls_after_two_truncated_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        truncated = _turn(("write_file", {}))
        truncated["stop_reason"] = "max_tokens"
        return truncated

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "build":
            return {"ok": False, "detail": "no product files"}
        return {"ok": False, "error": "write_file needs path + content"}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert calls == agent_native._MAX_TRUNCATED_WRITE_ABORT_AT
    assert result.done is False
    assert result.stop_reason == "oversized_write_red"
    assert "[OUTPUT LIMIT]" in str(result.transcript)


@pytest.mark.asyncio
async def test_stable_max_red_build_forces_targeted_edit_and_blocks_full_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    executed_reads = 0
    executed_writes = 0
    executed_edits = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function ProductApp(){return <main>bad</main>}",
                    },
                )
            )
        if calls == 2:
            return _turn(("build", {}))
        if calls == 3:
            assert {tool["name"] for tool in kwargs["tools"]} == {"edit_file"}
            assert "TARGETED COMPILER REPAIR" in str(convo[-1])
            assert "TS2322" in str(convo[-1])
            assert "<main>bad</main>" in str(convo[-1])
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "[OMITTED FROM HISTORY: 20000 characters already applied]",
                    },
                )
            )
        if calls == 4:
            assert "history placeholder" in str(convo[-1])
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function Recreated(){return null}",
                    },
                )
            )
        if calls == 5:
            assert "build is RED" in str(convo[-1])
            return _turn(
                (
                    "edit_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "old_string": "bad",
                        "new_string": "fixed",
                    },
                )
            )
        if calls == 6:
            assert {tool["name"] for tool in kwargs["tools"]} == {
                "read_file",
                "edit_file",
                "build",
            }
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function Recreated(){return null}",
                    },
                )
            )
        if calls == 7:
            assert "write_file remains disabled" in str(convo[-1])
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    builds = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal builds, executed_reads, executed_writes, executed_edits
        if action.name == "read_file":
            executed_reads += 1
            return {
                "ok": True,
                "content": "export default function ProductApp(){return <main>bad</main>}",
            }
        if action.name == "write_file":
            executed_writes += 1
            return {"ok": True, "content": action.args["content"]}
        if action.name == "edit_file":
            executed_edits += 1
            return {
                "ok": True,
                "content": "export default function ProductApp(){return <main>fixed</main>}",
            }
        if action.name == "build":
            builds += 1
            return {
                "ok": builds > 1,
                "detail": (
                    "clean"
                    if builds > 1
                    else "src/components/product/ProductApp.tsx(10,2): error TS2322: bad type"
                ),
            }
        return {"ok": True}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="build product",
        execute=execute,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert executed_reads == 0
    assert executed_writes == 1
    assert executed_edits == 1
    assert builds == 2
    assert result.files["src/components/product/ProductApp.tsx"].endswith("fixed</main>}")
    assert "build is RED" in str(result.transcript)


def test_typescript_repair_paths_include_named_local_type_dependency() -> None:
    output = (
        "src/components/product/catalog.ts(1,15): error TS2305: "
        "Module '\"./types\"' has no exported member 'Category'."
    )
    written = {
        "src/components/product/catalog.ts": "import type { Category } from './types';",
        "src/components/product/types.ts": "export interface MenuItem {}",
        "src/components/Elsewhere.tsx": "export const Elsewhere = 1;",
    }

    assert agent_native._typescript_repair_paths(output, written) == frozenset(
        {
            "src/components/product/catalog.ts",
            "src/components/product/types.ts",
        }
    )


def test_compact_repair_task_keeps_error_windows_not_entire_large_files() -> None:
    source = "\n".join(f"const line{i} = {i};" for i in range(1, 501))
    error = "src/components/product/ProductApp.tsx(250,7): error TS2322: bad"

    task = agent_native._stable_max_compact_repair_task(
        error,
        frozenset({"src/components/product/ProductApp.tsx"}),
        {"src/components/product/ProductApp.tsx": source},
    )

    assert "const line250 = 250;" in task
    assert "const line1 = 1;" not in task
    assert "const line500 = 500;" not in task
    assert "intentionally omit unrelated code" in task


@pytest.mark.asyncio
async def test_stable_max_compacts_existing_failing_file_after_one_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    legacy = "src/components/product/catalog.ts"
    broken = 'export const count: number = "bad";'
    fixed = "export const count: number = 1;"
    turns = iter(
        [
            _turn(("write_file", {"path": entry, "content": "export default function App(){}"})),
            _turn(("build", {})),
            _turn(("read_file", {"path": legacy})),
            _turn(
                (
                    "edit_file",
                    {"path": legacy, "search": broken, "replace": fixed},
                )
            ),
            _turn(("build", {})),
            _turn(("done", {"summary": "Готово"})),
        ]
    )
    calls: list[dict[str, Any]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(
            {
                "convo": json.loads(json.dumps(convo)),
                "tools": {str(tool["name"]) for tool in kwargs["tools"]},
            }
        )
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    builds = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal builds
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        if action.name == "read_file":
            return {"ok": True, "content": broken}
        if action.name == "edit_file":
            return {"ok": True, "content": fixed}
        if action.name == "build":
            builds += 1
            return {
                "ok": builds > 1,
                "detail": (
                    "clean"
                    if builds > 1
                    else f"{legacy}(1,14): error TS2322: Type 'string' is not assignable"
                ),
            }
        return {"ok": True}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="repair existing product",
        execute=execute,
        max_steps=10,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.files[legacy] == fixed
    assert calls[2]["tools"] == {"read_file", "edit_file"}
    assert calls[3]["tools"] == {"edit_file"}
    assert len(calls[3]["convo"]) == 1
    assert "TARGETED COMPILER REPAIR" in str(calls[3]["convo"][0])
    assert broken in str(calls[3]["convo"][0])


@pytest.mark.asyncio
async def test_stable_max_failed_exact_edit_allows_one_fresh_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    advertised: list[set[str]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        if calls == 1:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function App(){return <main>bad bad</main>}",
                    },
                )
            )
        if calls == 2:
            return _turn(("build", {}))
        if calls == 3:
            assert advertised[-1] == {"edit_file"}
            return _turn(
                (
                    "edit_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "search": "bad",
                        "replace": "fixed",
                    },
                )
            )
        if calls == 4:
            assert advertised[-1] == {"read_file", "edit_file"}
            return _turn(("read_file", {"path": "src/components/product/ProductApp.tsx"}))
        if calls == 5:
            assert advertised[-1] == {"edit_file"}
            return _turn(
                (
                    "edit_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "search": "<main>bad bad</main>",
                        "replace": "<main>fixed</main>",
                    },
                )
            )
        if calls == 6:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    builds = 0
    reads = 0
    edits = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal builds, reads, edits
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        if action.name == "read_file":
            reads += 1
            return {
                "ok": True,
                "content": "export default function App(){return <main>bad bad</main>}",
            }
        if action.name == "edit_file":
            edits += 1
            if edits == 1:
                return {"ok": False, "error": "search text must occur exactly once"}
            return {
                "ok": True,
                "content": "export default function App(){return <main>fixed</main>}",
            }
        if action.name == "build":
            builds += 1
            return {
                "ok": builds > 1,
                "detail": (
                    "clean"
                    if builds > 1
                    else "src/components/product/ProductApp.tsx(1,43): error TS2322: bad"
                ),
            }
        return {"ok": True}

    result = await agent_native.run_native_build(
        system="MAX runtime",
        task="repair product",
        execute=execute,
        max_steps=12,
        stable_max_loop=True,
    )

    assert result.done is True
    assert reads == 1
    assert edits == 2
    assert builds == 2
    assert result.files["src/components/product/ProductApp.tsx"].endswith("<main>fixed</main>}")


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
async def test_native_write_tracks_executor_sanitized_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/globals.css", "content": "raw"})),
            _turn(("done", {"summary": "done"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "sanitized", "detail": "written"}

    result = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=2,
    )

    assert result.files == {"src/app/globals.css": "sanitized"}


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
    assert res.stop_reason == "contract_green"
    assert "обязательные проверки" in res.summary
    assert set(res.files) == {"src/app/page.tsx", "src/components/Feature.tsx"}
    assert "Need a real feature component" in str(res.transcript)


@pytest.mark.asyncio
async def test_native_hard_stop_runs_missing_local_proofs_before_shipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green tree must not fail only because the provider turn ended before
    deterministic runtime, persistence and visual proof tool calls."""

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
        if evidence.get("see", 0) < 2:
            return "Run a second see after the visual fix."
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
    assert actions == ["write_file", "build", "runtime_check", "probe", "see", "see"]


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
async def test_generic_native_honours_configured_limit_and_forwards_trace_ids(
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
        max_steps=35,
    )

    assert len(calls) == 35
    assert res.stop_reason == "max_steps_green"
    assert calls[0]["stage"] == "build_plan"
    assert all(call["project_id"] == "22222222-2222-2222-2222-222222222222" for call in calls)
    assert all(call["run_id"] == "33333333-3333-3333-3333-333333333333" for call in calls)
    assert all(call["user_id"] == "11111111-1111-1111-1111-111111111111" for call in calls)


@pytest.mark.asyncio
async def test_stable_max_uses_durable_fuse_instead_of_generic_step_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(kwargs)
        call_number = len(calls)
        if call_number <= 31:
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            '"use client"; export default function Page() '
                            f"{{ return <main>{call_number}</main>; }}"
                        ),
                    },
                )
            )
        return _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
            "needs_fix": False,
        }

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        completion_check=complete,
        max_steps=1,
        model="claude-sonnet-5",
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    # The first product write immediately switches to build-only. Thirty
    # provider attempts may still reuse an older write schema, but none execute;
    # the final proof needs one following turn because same-turn runtime/see
    # calls were planned before the build result existed.
    assert len(calls) == 33
    assert calls[0]["model"] == "claude-sonnet-5"
    assert calls[0]["tools"] == agent_native._STABLE_MAX_PREENTRY_TOOLS_CACHED
    assert result.steps == 33


@pytest.mark.asyncio
async def test_stable_max_finishes_after_green_runtime_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertised: list[set[str]] = []
    turns = iter(
        [
            _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            '"use client"; export default function Page() '
                            "{ return <main>ready</main>; }"
                        ),
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
            "needs_fix": False,
        }

    def complete(files: Any, evidence: Any) -> str | None:
        if not files:
            return "source missing"
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check on the finished product after the last source write."
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        completion_check=complete,
        max_steps=10,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert advertised[2:] == [{"runtime_check"}]


@pytest.mark.asyncio
async def test_stable_max_focuses_green_build_on_remaining_source_contract_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    calls: list[dict[str, Any]] = []
    executed: list[str] = []
    initial = "export default function ProductApp(){return <main>bakery</main>}"
    fixed = (
        'import { getOmniaIntegrations } from "@/lib/omnia/integration-client"; '
        "export default function ProductApp(){ void getOmniaIntegrations(); "
        "return <main>bakery</main>}"
    )
    turns = iter(
        [
            _turn(("write_file", {"path": entry, "content": initial})),
            _turn(("build", {})),
            # A stale cached response tries to inspect again. The source-gap
            # executor must reject it without touching the project.
            _turn(("read_file", {"path": entry})),
            _turn(
                (
                    "edit_file",
                    {"path": entry, "search": initial, "replace": fixed},
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(
            {
                "convo": json.loads(json.dumps(convo)),
                "tools": {str(tool["name"]) for tool in kwargs["tools"]},
            }
        )
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        executed.append(action.name)
        if action.name == "edit_file":
            return {"ok": True, "content": fixed, "detail": "integration added"}
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
            "needs_fix": False,
        }

    def complete(files: Any, evidence: Any) -> str | None:
        source = files.get(entry, "")
        if not source:
            return "source missing"
        if "getOmniaIntegrations" not in source:
            return (
                "The brief names an external integration. Import and await "
                "getOmniaIntegrations from @/lib/omnia/integration-client."
            )
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check on the finished product after the last source write."
        if not evidence.get("see_after_write"):
            return "Run see once through the signed MAX preview after the last source write."
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a connected bakery application.",
        execute=execute,
        completion_check=complete,
        max_steps=12,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert result.files[entry] == fixed
    assert "read_file" not in executed
    assert calls[2]["tools"] == {"write_file", "edit_file"}
    assert calls[3]["tools"] == {"write_file", "edit_file"}
    assert len(calls[2]["convo"]) == 1
    assert "[FOCUSED SOURCE CONTRACT REPAIR]" in str(calls[2]["convo"][0])
    assert "getOmniaIntegrations" in str(calls[2]["convo"][0])


@pytest.mark.asyncio
async def test_stable_max_rejects_and_bounds_byte_identical_source_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    initial = "export default function ProductApp(){return <main>bakery</main>}"
    turns = iter(
        [
            _turn(("write_file", {"path": entry, "content": initial})),
            _turn(("build", {})),
            _turn(("edit_file", {"path": entry, "search": "bakery", "replace": "bakery"})),
            _turn(("edit_file", {"path": entry, "search": "bakery", "replace": "bakery"})),
        ]
    )
    calls: list[dict[str, Any]] = []
    builds = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(json.loads(json.dumps(convo)))
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal builds
        if action.name == "build":
            builds += 1
            return {"ok": True, "detail": "typecheck clean"}
        return {"ok": True, "content": initial, "detail": "edited"}

    def complete(files: Any, evidence: Any) -> str | None:
        if "getOmniaIntegrations" not in files.get(entry, ""):
            return "Import getOmniaIntegrations before finishing."
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a connected bakery application.",
        execute=execute,
        completion_check=complete,
        max_steps=12,
        stable_max_loop=True,
    )

    assert result.done is False
    assert result.stop_reason == "noop_write_red"
    assert result.files[entry] == initial
    assert builds == 2  # initial build plus one local terminal proof, never one per no-op
    assert len(calls) == 4
    assert "byte-identical" in json.dumps(calls[3])


def test_stable_max_source_gap_classifier_preserves_proof_lifecycle() -> None:
    assert agent_native._is_stable_max_source_gap(
        "The brief names an external integration; import getOmniaIntegrations."
    )
    assert not agent_native._is_stable_max_source_gap("proof missing")
    assert not agent_native._is_stable_max_source_gap(
        "Read visual-evaluation after the first rendered see."
    )
    assert not agent_native._is_stable_max_source_gap(
        "Run runtime_check on the finished product after the last source write."
    )


@pytest.mark.asyncio
async def test_stable_max_reopens_editing_after_actionable_visual_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertised: list[set[str]] = []
    turns = iter(
        [
            _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            '"use client"; export default function Page() '
                            "{ return <main>first</main>; }"
                        ),
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            '"use client"; export default function Page() '
                            "{ return <main>polished</main>; }"
                        ),
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )
    see_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Increase the mobile title width.",
            }
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    def complete(files: Any, evidence: Any) -> str | None:
        if not files:
            return "source missing"
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check on the finished product after the last source write."
        if not evidence.get("see_after_write"):
            return "Run see once through the signed MAX preview after the last source write."
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        completion_check=complete,
        max_steps=12,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert result.files[agent_native._STABLE_MAX_PRODUCT_ENTRY].endswith(
        "{ return <main>polished</main>; }"
    )
    assert advertised[2] == {"runtime_check"}
    assert "see" in advertised[3]
    assert "write_file" in advertised[4]
    assert advertised[6] == {"runtime_check"}
    assert "see" in advertised[7]


@pytest.mark.asyncio
async def test_stable_max_compacts_visual_repair_around_current_source_and_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    turns = iter(
        [
            _turn(("write_file", {"path": entry, "content": "first-screen"})),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "edit_file",
                    {"path": entry, "search": "first-screen", "replace": "polished-screen"},
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )
    calls: list[dict[str, Any]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(
            {
                "convo_len": len(convo),
                "prompt": convo[0]["content"] if len(convo) == 1 else "",
                "tools": {str(t["name"]) for t in kwargs["tools"]},
            }
        )
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    see_calls = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Increase CTA contrast and reduce the mobile heading.",
            }
        if action.name == "edit_file":
            return {"ok": True, "content": "polished-screen", "detail": "edited"}
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a premium training companion.",
        execute=execute,
        completion_check=complete,
        max_steps=20,
        stable_max_loop=True,
    )

    rescue = calls[4]
    assert rescue["convo_len"] == 1
    assert "[FOCUSED VISUAL RESCUE]" in rescue["prompt"]
    assert "Increase CTA contrast" in rescue["prompt"]
    assert "first-screen" in rescue["prompt"]
    assert "Never fix a hidden CTA by floating it over scrollable choices" in rescue["prompt"]
    assert "Пользователь/User/Guest" in rescue["prompt"]
    assert rescue["tools"] == {"write_file", "edit_file", "generate_media"}
    assert result.done is True
    assert result.files[entry] == "polished-screen"


@pytest.mark.asyncio
async def test_stable_max_can_generate_and_embed_required_visual_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    turns = iter(
        [
            _turn(("write_file", {"path": entry, "content": '<div className="dish" />'})),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "generate_media",
                    {"kind": "image", "prompt": "warm editorial bakery photography"},
                ),
                (
                    "generate_media",
                    {"kind": "image", "prompt": "duplicate asset must be rejected"},
                ),
            ),
            _turn(
                (
                    "edit_file",
                    {
                        "path": entry,
                        "search": '<div className="dish" />',
                        "replace": '<img src="https://cdn.example/bakery.webp" alt="Выпечка" />',
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )
    advertised: list[set[str]] = []
    executed: list[str] = []
    see_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        executed.append(action.name)
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Replace the food icon with real bakery photography.",
            }
        if action.name == "generate_media":
            return {
                "ok": True,
                "url": "https://cdn.example/bakery.webp",
                "content": "https://cdn.example/bakery.webp",
            }
        if action.name == "edit_file":
            return {
                "ok": True,
                "content": '<img src="https://cdn.example/bakery.webp" alt="Выпечка" />',
                "detail": "edited",
            }
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a premium bakery ordering app.",
        execute=execute,
        completion_check=complete,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert executed.count("generate_media") == 1
    assert "https://cdn.example/bakery.webp" in result.files[entry]
    assert advertised[4] == {"write_file", "edit_file", "generate_media"}
    assert advertised[5] == {"write_file", "edit_file", "generate_media"}


@pytest.mark.asyncio
async def test_stable_max_allows_one_css_finish_turn_after_component_visual_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for production run 1f7cad03: component edits landed, but the
    immediately-forced build rejected the model's following CTA/safe-area CSS
    edits. The same visual defects then survived every proof cycle."""

    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    stylesheet = "src/app/globals.css"
    turns = iter(
        [
            _turn(
                ("write_file", {"path": entry, "content": "first-screen"}),
                (
                    "write_file",
                    {"path": stylesheet, "content": '@import "tailwindcss";\n.old{}'},
                ),
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "edit_file",
                    {
                        "path": entry,
                        "search": "first-screen",
                        "replace": "polished-screen",
                    },
                )
            ),
            _turn(
                (
                    "edit_file",
                    {
                        "path": stylesheet,
                        "search": ".old{}",
                        "replace": ".cta{width:100%;background:#f43}",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )
    advertised: list[set[str]] = []
    executed: list[tuple[str, str]] = []
    see_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        executed.append((action.name, action.path))
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Make CTA full-width and add bottom safe-area padding.",
            }
        if action.name == "edit_file" and action.path == entry:
            return {"ok": True, "content": "polished-screen", "detail": "edited"}
        if action.name == "edit_file" and action.path == stylesheet:
            return {
                "ok": True,
                "content": '@import "tailwindcss";\n.cta{width:100%;background:#f43}',
                "detail": "edited",
            }
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a premium training companion.",
        execute=execute,
        completion_check=complete,
        max_steps=20,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.files[entry] == "polished-screen"
    assert ".cta{width:100%" in result.files[stylesheet]
    assert advertised[5] == {"edit_file", "build"}
    assert advertised[6] == {"build"}
    assert ("edit_file", stylesheet) in executed


@pytest.mark.asyncio
async def test_stable_max_css_only_visual_repair_resumes_rendered_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    stylesheet = "src/app/globals.css"
    turns = iter(
        [
            _turn(
                ("write_file", {"path": entry, "content": "screen-first"}),
                ("write_file", {"path": stylesheet, "content": "css-first"}),
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "edit_file",
                    {"path": stylesheet, "search": "css-first", "replace": "css-polished"},
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )
    advertised: list[set[str]] = []
    executed: list[tuple[str, str]] = []
    see_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        executed.append((action.name, action.path))
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Fix the collapsed layout and broken bottom navigation styles.",
            }
        if action.name == "edit_file" and action.path == stylesheet:
            return {"ok": True, "content": "css-polished", "detail": "edited"}
        if action.name == "edit_file" and action.path == entry:
            return {"ok": True, "content": "screen-polished", "detail": "edited"}
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        if not files:
            return "product missing"
        if not evidence.get("build_after_write"):
            return "Run build after the last write."
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check after the last write."
        if not evidence.get("see_after_write"):
            return "Run see on / after the final product write."
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a premium bakery ordering app.",
        execute=execute,
        completion_check=complete,
        max_steps=8,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.files[entry] == "screen-first"
    assert result.files[stylesheet] == "css-polished"
    assert ("edit_file", stylesheet) in executed
    assert advertised[4] == {"write_file", "edit_file", "generate_media"}
    assert advertised[5] == {"build"}
    assert advertised[6] == {"runtime_check"}
    assert "see" in advertised[7]


@pytest.mark.asyncio
async def test_stable_max_stops_after_bounded_unsuccessful_visual_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    turns: list[dict[str, Any]] = [
        _turn(("write_file", {"path": entry, "content": "screen-0"})),
        _turn(("build", {})),
        _turn(("runtime_check", {"path": "/"}), ("see", {"path": "/"})),
    ]
    for attempt in range(1, agent_native._STABLE_MAX_VISUAL_REPAIR_LIMIT + 1):
        turns.extend(
            [
                _turn(
                    (
                        "write_file",
                        {"path": entry, "content": f"screen-{attempt}"},
                    )
                ),
                _turn(("build", {})),
                _turn(("runtime_check", {"path": "/"}), ("see", {"path": "/"})),
            ]
        )
    call_count = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal call_count
        response = turns[call_count]
        call_count += 1
        return response

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    see_calls = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": True,
                "detail": f"Visual verdict {see_calls} remains below the floor.",
            }
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build the complete app.",
        execute=execute,
        completion_check=complete,
        max_steps=40,
        stable_max_loop=True,
    )

    assert result.done is False
    assert result.stop_reason == "visual_quality_unmet"
    assert "после двух" in result.summary
    assert result.files[entry] == f"screen-{agent_native._STABLE_MAX_VISUAL_REPAIR_LIMIT}"
    assert call_count == 3 + (3 * agent_native._STABLE_MAX_VISUAL_REPAIR_LIMIT)
    assert see_calls == 1 + agent_native._STABLE_MAX_VISUAL_REPAIR_LIMIT


@pytest.mark.asyncio
async def test_stable_max_rehydrates_visual_source_after_history_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = agent_native._STABLE_MAX_PRODUCT_ENTRY
    calls: list[dict[str, Any]] = []
    turns = iter(
        [
            _turn(("write_file", {"path": entry, "content": "first-screen"})),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "edit_file",
                    {
                        "path": entry,
                        "old_string": "[OMITTED FROM HISTORY: 1200 characters already applied]",
                        "new_string": "polished-screen",
                    },
                )
            ),
            _turn(
                (
                    "edit_file",
                    {
                        "path": entry,
                        "old_string": "first-screen",
                        "new_string": "polished-screen",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(
            {
                "convo": json.loads(json.dumps(convo)),
                "tools": {str(t["name"]) for t in kwargs["tools"]},
            }
        )
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    see_calls = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Increase CTA contrast.",
            }
        if action.name == "edit_file":
            return {"ok": True, "content": "polished-screen", "detail": "edited"}
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="Build a premium training companion.",
        execute=execute,
        completion_check=complete,
        max_steps=20,
        stable_max_loop=True,
    )

    assert calls[4]["tools"] == {"write_file", "edit_file", "generate_media"}
    assert len(calls[5]["convo"]) == 1
    assert "[FOCUSED VISUAL RESCUE]" in str(calls[5]["convo"][0])
    assert "first-screen" in str(calls[5]["convo"][0])
    assert result.done is True
    assert result.files[entry] == "polished-screen"


def test_stable_max_normalizes_only_known_leading_slash_paths() -> None:
    repaired = agent_native._normalize_stable_max_action_path(
        agent_native.Action(
            name="edit_file",
            args={"path": "/src/components/product/ProductApp.tsx"},
        )
    )
    arbitrary = agent_native._normalize_stable_max_action_path(
        agent_native.Action(name="write_file", args={"path": "/etc/passwd"})
    )
    duplicate = agent_native._normalize_stable_max_action_path(
        agent_native.Action(name="write_file", args={"path": "//src/components/product/x.tsx"})
    )

    assert repaired.path == "src/components/product/ProductApp.tsx"
    assert arbitrary.path == "/etc/passwd"
    assert duplicate.path == "//src/components/product/x.tsx"


@pytest.mark.asyncio
async def test_stable_max_forces_known_visual_repair_after_one_inspection_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advertised: list[set[str]] = []
    executed: list[str] = []
    turns = iter(
        [
            _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": "first",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
            _turn(("read_file", {"path": agent_native._STABLE_MAX_PRODUCT_ENTRY})),
            _turn(("grep", {"path": "src", "query": "hero"})),
            _turn(
                (
                    "edit_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "old_string": "first",
                        "new_string": "polished",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("see", {"path": "/"})),
        ]
    )
    see_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        executed.append(action.name)
        if action.name == "see":
            see_calls += 1
            return {
                "ok": see_calls != 1,
                "verdict": "generic" if see_calls == 1 else "beautiful",
                "score": 4 if see_calls == 1 else 9,
                "needs_fix": see_calls == 1,
                "detail": (
                    "Make the hero compact. BROWSER SIGNALS: GET /hero.jpg 404"
                    if see_calls == 1
                    else "clean"
                ),
            }
        if action.name == "edit_file":
            return {"ok": True, "content": "polished", "detail": "updated"}
        return {
            "ok": True,
            "content": action.args.get("content", "first"),
            "detail": "clean",
        }

    def complete(files: Any, evidence: Any) -> str | None:
        if not files:
            return "source missing"
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check on the finished product after the last source write."
        if not evidence.get("see_after_write"):
            return "Run see once through the signed MAX preview after the last source write."
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        completion_check=complete,
        max_steps=14,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert result.files[agent_native._STABLE_MAX_PRODUCT_ENTRY] == "polished"
    assert advertised[5:7] == [
        {"write_file", "edit_file", "generate_media"},
        {"write_file", "edit_file", "generate_media"},
    ]
    assert "grep" not in executed


@pytest.mark.asyncio
async def test_max_runtime_stops_after_provider_failure_without_paid_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider outage")
        if calls <= agent_native._NO_WRITE_ABORT_AT + 2:
            return _turn(("read_file", {"path": "src/app/page.tsx"}))
        if calls == agent_native._NO_WRITE_ABORT_AT + 3:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/app/page.tsx",
                        "content": (
                            '"use client"; export default function Page() '
                            "{ return <main>ready</main>; }"
                        ),
                    },
                )
            )
        return _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", "source"),
            "detail": "clean",
            "needs_fix": False,
        }

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        completion_check=complete,
        max_steps=1,
    )

    assert result.done is False
    assert result.stop_reason == "provider_stopped_red"
    assert calls == 1


@pytest.mark.asyncio
async def test_max_runtime_stops_at_limit_during_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls <= agent_native._INFRA_DEAD_ABORT_AT:
            return _turn(("read_file", {"path": "src/app/page.tsx"}))
        if calls == agent_native._INFRA_DEAD_ABORT_AT + 1:
            return _turn(
                (
                    "write_file",
                    {"path": "src/app/page.tsx", "content": "restored product source"},
                )
            )
        return _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    async def execute(action: Any) -> dict[str, Any]:
        if calls <= agent_native._INFRA_DEAD_ABORT_AT:
            return {"ok": False, "error": "orchestrator restarting", "infra_dead": True}
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
            "needs_fix": False,
        }

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        completion_check=complete,
        max_steps=1,
    )

    assert result.done is False
    assert result.stop_reason == "max_steps_red"
    assert calls == 1


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
async def test_max_runtime_stops_on_permanent_provider_rejection_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"provider": 0, "build": 0}
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["provider"] += 1
        raise agent_native.PermanentProviderError(402)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        assert action.name == "build"
        calls["build"] += 1
        return {"ok": True, "detail": "clean"}

    def incomplete(_files: Any, _evidence: Any) -> str | None:
        return "product source is incomplete"

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    res = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        emit=emit,
        completion_check=incomplete,
        max_steps=None,
    )

    assert calls == {"provider": 1, "build": 1}
    assert res.done is False
    assert res.stop_reason == "provider_rejected_red"
    assert any(
        event == "agent.step"
        and data.get("action") == "provider_rejected"
        and "HTTP 402" in str(data.get("detail"))
        for event, data in events
    )


@pytest.mark.asyncio
async def test_max_runtime_stops_on_spend_budget_without_another_provider_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"provider": 0, "build": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["provider"] += 1
        raise agent_native.SpendBudgetExceeded

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        assert action.name == "build"
        calls["build"] += 1
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        max_steps=None,
    )

    assert calls == {"provider": 1, "build": 1}
    assert res.done is True
    assert res.stop_reason == "spend_budget_green"


@pytest.mark.asyncio
async def test_max_runtime_stops_after_bounded_provider_reconnect_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"provider": 0, "build": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["provider"] += 1
        raise RuntimeError("temporary upstream outage")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    async def execute(action: Any) -> dict[str, Any]:
        assert action.name == "build"
        calls["build"] += 1
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build the complete app",
        execute=execute,
        max_steps=None,
    )

    assert calls == {"provider": 1, "build": 1}
    assert res.done is True
    assert res.stop_reason == "provider_stopped_green"


@pytest.mark.asyncio
async def test_max_runtime_stops_after_three_malformed_provider_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"provider": 0, "build": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["provider"] += 1
        return {"content": None}

    async def no_sleep(_seconds: float) -> None:
        return None

    async def execute(action: Any) -> dict[str, Any]:
        assert action.name == "build"
        calls["build"] += 1
        return {"ok": True, "detail": "clean"}

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt(
            "MAX PLATFORM CORE CONTRACT",
            reference_max_loop=True,
        ),
        task="build",
        execute=execute,
        completion_check=lambda _files, _evidence: "product missing",
        reference_max_loop=True,
        max_steps=1,
    )

    assert calls == {
        "provider": agent_native._MAX_PROVIDER_RECONNECT_CYCLES,
        "build": 1,
    }
    assert result.done is False
    assert result.stop_reason == "provider_stopped_red"


@pytest.mark.asyncio
async def test_reference_max_loop_is_not_cut_off_at_turn_40(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls <= 40:
            return _turn(("read_file", {"path": "src/app/page.tsx"}))
        if calls == 41:
            return _turn(
                (
                    "write_file",
                    {"path": "src/app/page.tsx", "content": "product source"},
                )
            )
        return _turn(("build", {}), ("runtime_check", {"path": "/"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", "existing source"),
            "detail": "green",
        }

    def completion(files: Any, evidence: Any) -> str | None:
        if not files:
            return "write product"
        if evidence.get("runtime_check_after_write", 0) < 1:
            return "run runtime_check"
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt(
            "MAX PLATFORM CORE CONTRACT",
            reference_max_loop=True,
        ),
        task="build",
        execute=execute,
        completion_check=completion,
        reference_max_loop=True,
        max_steps=1,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert calls == 42


@pytest.mark.asyncio
async def test_reference_max_loop_finishes_only_after_clean_build_and_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "too early"})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("done", {"summary": "ready"})),
        ]
    )
    advertised: set[str] = set()

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.update(str(tool["name"]) for tool in kwargs["tools"])
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "write_file":
            return {"ok": True, "content": "page"}
        return {"ok": True, "detail": "green"}

    def completion(files: Any, evidence: Any) -> str | None:
        if not files:
            return "write the product"
        if evidence.get("runtime_check_after_write", 0) < 1:
            return "run runtime_check"
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt(
            "MAX PLATFORM CORE CONTRACT",
            reference_max_loop=True,
        ),
        task="build",
        execute=execute,
        completion_check=completion,
        reference_max_loop=True,
        max_steps=40,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert result.steps == 4
    assert advertised == agent_native._MAX_REFERENCE_TOOL_NAMES


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 404, 422])
@pytest.mark.asyncio
async def test_messages_call_fails_fast_on_permanent_4xx(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    calls = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        return httpx.Response(status_code, request=request)

    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    try:
        with pytest.raises(agent_native.PermanentProviderError) as exc_info:
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
            )
    finally:
        await client.aclose()

    assert exc_info.value.status_code == status_code
    assert calls == 1


@pytest.mark.asyncio
async def test_messages_call_never_retries_ambiguous_paid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        return httpx.Response(
            503,
            request=request,
            json={"error": {"type": "paid_call_ambiguous"}},
        )

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    try:
        with pytest.raises(agent_native.AmbiguousPaidCallError):
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
            )
    finally:
        await client.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_messages_call_maps_provider_body_timeout_with_turn_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs["json"]["metadata"])
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        return httpx.Response(
            504,
            request=request,
            json={"error": {"type": "provider_response_timeout"}},
        )

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    try:
        with pytest.raises(agent_native.ProviderResponseTimeoutError):
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
                turn_id="run-1:7",
                resume_count=1,
            )
    finally:
        await client.aclose()

    assert captured["turn_id"] == "run-1:7"
    assert captured["resume_count"] == 1


@pytest.mark.asyncio
async def test_stable_max_resumes_classified_body_timeout_before_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((kwargs["turn_id"], kwargs["resume_count"]))
        if len(calls) == 1:
            raise agent_native.ProviderResponseTimeoutError(504)
        if len(calls) == 2:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": (
                            "export default function ProductApp(){return <main>Ready</main>}"
                        ),
                    },
                )
            )
        if len(calls) == 3:
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    result = await agent_native.run_native_build(
        system="MAX starter",
        task="build product",
        execute=execute,
        run_id="run-1",
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "done"
    assert calls[:2] == [("run-1:0", 0), ("run-1:0", 1)]
    assert calls[2][0] == "run-1:1"


@pytest.mark.asyncio
async def test_stable_max_retries_ambiguous_free_turn_with_same_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((kwargs["turn_id"], kwargs["resume_count"]))
        if len(calls) == 1:
            raise agent_native.AmbiguousPaidCallError(503)
        if len(calls) == 2:
            return _turn(
                (
                    "write_file",
                    {
                        "path": agent_native._STABLE_MAX_PRODUCT_ENTRY,
                        "content": (
                            "export default function ProductApp(){return <main>Ready</main>}"
                        ),
                    },
                )
            )
        return _turn(("build", {}))

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    result = await agent_native.run_native_build(
        system="MAX starter",
        task="build product",
        execute=execute,
        run_id="free-run-1",
        free=True,
        emit=emit,
        completion_check=lambda written, evidence: messages._reference_max_completion_gap(
            written,
            evidence,
            require_product_entry=True,
        ),
        stable_max_loop=True,
    )

    assert calls[:2] == [("free-run-1:0", 0), ("free-run-1:0", 1)]
    assert calls[2][0] == "free-run-1:1"
    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert any(data.get("action") == "provider_resume" for _event, data in events)
    assert not any(data.get("action") == "accounting_guard" for _event, data in events)


@pytest.mark.asyncio
async def test_active_max_safe_surface_survives_timeout_and_reaches_verified_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    timed_out = False
    entry = (
        'export default function ProductApp(){return <main className="app-shell">'
        '<header className="app-header"><h1 className="hero-title">Product</h1></header>'
        '<button className="primary-action">Start</button>'
        + ("complete product flow " * 30)
        + "</main>}"
    )
    styles = (
        ".app-shell{min-height:100dvh}.app-header{padding:24px}"
        ".hero-title{font-size:32px}.primary-action{min-height:44px}"
        + ("/* product visual system */" * 20)
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal timed_out
        calls.append(
            {
                "turn_id": kwargs["turn_id"],
                "resume_count": kwargs["resume_count"],
                "tools": {str(tool["name"]) for tool in kwargs["tools"]},
            }
        )
        call_number = len(calls)
        if call_number == 1:
            return _turn(
                ("read_file", {"path": "src/components/product/ProductApp.tsx"}),
                ("grep", {"pattern": "data-omnia-native-legal-nav", "path": "src"}),
            )
        if call_number == 2:
            return _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": entry,
                    },
                ),
                (
                    "write_file",
                    {"path": "src/app/globals.css", "content": styles},
                ),
            )
        if not timed_out:
            timed_out = True
            raise agent_native.ProviderResponseTimeoutError(504)
        if call_number == 4:
            return _turn(("build", {}))
        if call_number == 5:
            return _turn(("runtime_check", {"path": "/"}))
        return _turn(("see", {"path": "/"}))

    executed: list[str] = []

    async def execute(action: Any) -> dict[str, Any]:
        executed.append(action.name)
        return {
            "ok": True,
            "content": action.args.get("content", "approved read-only evidence"),
            "detail": "green",
        }

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt(
            "MAX PLATFORM CORE CONTRACT",
            "MAX capability catalog: `product-flow`",
            stable_max_loop=True,
        ),
        task="build product",
        execute=execute,
        run_id="run-active-max",
        completion_check=lambda written, evidence: messages._reference_max_completion_gap(
            written,
            evidence,
            require_product_entry=True,
        ),
        stable_max_loop=True,
    )

    assert calls[0]["tools"] == {
        tool["name"] for tool in agent_native._STABLE_MAX_PREENTRY_TOOLS_CACHED
    }
    assert calls[2]["turn_id"] == calls[3]["turn_id"]
    assert (calls[2]["resume_count"], calls[3]["resume_count"]) == (0, 1)
    assert {
        "read_file",
        "grep",
        "write_file",
        "build",
    } <= set(executed)
    assert result.done is True
    assert result.stop_reason == "contract_green"


@pytest.mark.asyncio
async def test_stable_max_native_turn_limit_is_finite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = agent_native.get_settings()
    monkeypatch.setattr(settings, "agent_builder_max_runtime_steps", 3)
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _turn(("read_file", {"path": "src/app.tsx"}))

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "source", "detail": "clean"}

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    result = await agent_native.run_native_build(
        system="MAX starter",
        task="build product",
        execute=execute,
        stable_max_loop=True,
    )

    assert calls == 3
    assert result.stop_reason == "max_steps_green"


@pytest.mark.parametrize("content", [None, [], [{"type": "unknown"}]])
@pytest.mark.asyncio
async def test_messages_call_never_retries_malformed_paid_success(
    monkeypatch: pytest.MonkeyPatch,
    content: Any,
) -> None:
    calls = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        return httpx.Response(200, request=request, json={"content": content})

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    try:
        with pytest.raises(agent_native.AmbiguousPaidCallError):
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
            )
    finally:
        await client.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_messages_call_never_retries_after_response_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        raise httpx.ReadTimeout("response lost", request=request)

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    try:
        with pytest.raises(agent_native.AmbiguousPaidCallError) as exc_info:
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
            )
    finally:
        await client.aclose()

    assert exc_info.value.status_code is None
    assert calls == 1


@pytest.mark.asyncio
async def test_messages_call_maps_run_budget_409_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        return httpx.Response(
            409,
            request=request,
            json={
                "type": "error",
                "error": {
                    "type": "run_budget_exhausted",
                    "message": "safe spend limit reached",
                },
            },
        )

    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    try:
        with pytest.raises(agent_native.SpendBudgetExceeded):
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
            )
    finally:
        await client.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_messages_call_treats_unstructured_5xx_as_ambiguous_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        return httpx.Response(503, request=request)

    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    try:
        with pytest.raises(agent_native.AmbiguousPaidCallError):
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "build"}],
                "system",
            )
    finally:
        await client.aclose()

    assert calls == 1


def test_native_agent_has_eyes_and_taste() -> None:
    """Smart-agent contract (deep-research 2026-07-17): the native builder must
    ADVERTISE the `see` vision tool (screenshot → design self-critique) AND carry
    design-system + think-first rules in its system prompt — the two levers that
    lift TASTE and cut bugs. A dropped `see` or a stripped taste block silently
    reverts the agent to «компилируется, но уродливо»."""
    from omnia_api.services import agent_builder as B

    names = [t["name"] for t in agent_native._TOOLS]
    assert "see" in names, "native agent must offer the `see` vision-critique tool"
    # Dead schema is worse than none — the executor must actually route `see`.
    assert "see" in B._KNOWN_ACTIONS, "executor must route the `see` action"

    sysp = agent_native.native_system_prompt("STACK GUIDE", None)
    assert "ВКУС В ДИЗАЙНЕ" in sysp, "design-taste + see-loop rules must be present"
    assert "root-cause" in sysp, "think-before-fix (fewer-bugs) rule must be present"
    assert "`see` главный" in agent_native._NATIVE_PREAMBLE  # visual-critique cycle


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


def test_successful_file_mutation_observation_does_not_echo_whole_source() -> None:
    source = "export const value = 1;\n" * 2_000

    result = agent_native._obs_to_tool_result(
        "tu_write",
        {"ok": True, "content": source},
        tool_name="edit_file",
    )

    assert len(str(result["content"])) < 300
    assert str(len(source)) in str(result["content"])
    assert "export const" not in str(result["content"])


def test_native_observation_has_stable_harness_shape() -> None:
    result = agent_native._obs_to_tool_result(
        "tu_build",
        {"ok": False, "error": "TS2307 missing module"},
        tool_name="build",
    )
    payload = json.loads(result["content"])

    assert result["is_error"] is True
    assert payload == {
        "status": "error",
        "summary": "TS2307 missing module",
        "next_actions": [
            "Use the root-cause hint once; stop repeating the identical failing call."
        ],
        "artifacts": [],
        "data": "TS2307 missing module",
    }


@pytest.mark.asyncio
async def test_max_lifecycle_enforces_unique_skills_and_visual_review_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "too early"})),
            _turn(
                *(
                    ("read_skill", {"skill": skill, "reason": "required"})
                    for skill in MAX_REQUIRED_PREWRITE_SKILLS
                )
            ),
            _turn(("read_skill", {"skill": "product-flow", "reason": "duplicate"})),
            _turn(
                ("read_skill", {"skill": "domain-fitness", "reason": "domain"}),
                ("read_skill", {"skill": "trust-safety", "reason": "health"}),
                ("read_skill", {"skill": "growth-analytics", "reason": "growth"}),
                ("read_skill", {"skill": "interaction-motion", "reason": "over budget"}),
            ),
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "product"})),
            _turn(("build", {})),
            _turn(
                (
                    "read_skill",
                    {"skill": MAX_REQUIRED_POST_SEE_SKILL, "reason": "too early"},
                )
            ),
            _turn(("see", {"path": "/"})),
            _turn(
                (
                    "read_skill",
                    {"skill": MAX_REQUIRED_POST_SEE_SKILL, "reason": "rendered review"},
                ),
                ("done", {"summary": "same turn is premature"}),
            ),
            _turn(("done", {"summary": "review applied"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    executed: list[tuple[str, str]] = []

    async def execute(action: Any) -> dict[str, Any]:
        executed.append((action.name, str(action.args.get("skill") or action.path)))
        return {
            "ok": True,
            "content": action.args.get("content", "loaded"),
            "detail": "green",
        }

    def check(files: Any, evidence: Any) -> str | None:
        for skill in (*MAX_REQUIRED_PREWRITE_SKILLS, MAX_REQUIRED_POST_SEE_SKILL):
            if evidence.get(f"skill:{skill}", 0) < 1:
                return f"missing {skill}"
        if evidence.get("visual_evaluation_after_see", 0) < 1:
            return "visual evaluation result is not applied yet"
        return None if files.get("src/app/page.tsx") == "product" else "missing product"

    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build",
        execute=execute,
        completion_check=check,
        enforce_max_skill_lifecycle=True,
        max_steps=None,
    )

    assert result.done is True
    assert result.stop_reason == "done"
    assert result.summary == "review applied"
    assert ("write_file", "src/app/page.tsx") in executed
    assert executed.count(("write_file", "src/app/page.tsx")) == 1
    assert executed.count(("read_skill", "product-flow")) == 1
    assert ("read_skill", "interaction-motion") not in executed
    assert executed.count(("read_skill", MAX_REQUIRED_POST_SEE_SKILL)) == 1
    assert executed.index(("see", "/")) < executed.index(
        ("read_skill", MAX_REQUIRED_POST_SEE_SKILL)
    )


@pytest.mark.asyncio
async def test_completion_contract_finishes_without_ceremonial_provider_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"})),
            _turn(
                ("build", {}),
                ("runtime_check", {"path": "/"}),
                ("see", {"path": "/"}),
            ),
        ]
    )
    calls = 0
    advertised_tools: list[set[str]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        advertised_tools.append({tool["name"] for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="t",
        execute=execute,
        completion_check=complete,
        max_steps=10,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert calls == 2
    assert all({"probe", "verify_isolation"} <= names for names in advertised_tools)
    assert all("read_skill" not in names for names in advertised_tools)


@pytest.mark.asyncio
async def test_runtime_failure_reopens_source_repair_instead_of_proof_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function ProductApp(){return <main/>}",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(
                (
                    "edit_file",
                    {
                        "path": "src/app/globals.css",
                        "search": ":global(.bad)",
                        "replace": ".good",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
        ]
    )
    advertised: list[set[str]] = []
    runtime_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal runtime_calls
        if action.name == "runtime_check":
            runtime_calls += 1
            if runtime_calls == 1:
                return {
                    "ok": False,
                    "status_code": 500,
                    "detail": "src/app/globals.css:343 invalid pseudo-class",
                }
        if action.name == "edit_file":
            return {"ok": True, "content": ".good {}"}
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "green",
        }

    def complete(files: Any, evidence: Any) -> str | None:
        if not files:
            return "write product"
        if not evidence.get("build_after_write"):
            return "Run build"
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check"
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build",
        execute=execute,
        completion_check=complete,
        max_steps=10,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert runtime_calls == 2
    assert "edit_file" in advertised[3]
    assert "runtime_check" not in advertised[3]


@pytest.mark.asyncio
async def test_unavailable_runtime_retries_proof_without_reopening_source_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(
                (
                    "write_file",
                    {
                        "path": "src/components/product/ProductApp.tsx",
                        "content": "export default function ProductApp(){return <main/>}",
                    },
                )
            ),
            _turn(("build", {})),
            _turn(("runtime_check", {"path": "/"})),
            _turn(("runtime_check", {"path": "/"})),
        ]
    )
    advertised: list[set[str]] = []
    runtime_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        advertised.append({str(tool["name"]) for tool in kwargs["tools"]})
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal runtime_calls
        if action.name == "runtime_check":
            runtime_calls += 1
            if runtime_calls == 1:
                return {
                    "ok": False,
                    "infra_dead": True,
                    "detail": "route / did not answer yet",
                }
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "green",
        }

    def complete(files: Any, evidence: Any) -> str | None:
        if not files:
            return "write product"
        if not evidence.get("build_after_write"):
            return "Run build"
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check"
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="build",
        execute=execute,
        completion_check=complete,
        max_steps=8,
        stable_max_loop=True,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert runtime_calls == 2
    assert advertised[3] == {"runtime_check"}


@pytest.mark.asyncio
async def test_unavailable_see_does_not_satisfy_completion_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"})),
            _turn(
                ("build", {}),
                ("runtime_check", {"path": "/"}),
                ("see", {"path": "/"}),
                ("see", {"path": "/"}),
            ),
            _turn(("done", {"summary": "finished"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    see_calls = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "proof_unavailable": True,
                "detail": "visual QA unavailable",
            }
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        if not files or not evidence.get("build_after_write"):
            return "proof missing"
        if not evidence.get("runtime_check_after_write"):
            return "Run runtime_check"
        if not evidence.get("see_after_write"):
            return "Run see on / after the final product write"
        return None

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt(
            "MAX VERIFICATION OVERRIDE\nMAX PLATFORM CORE CONTRACT"
        ),
        task="t",
        execute=execute,
        completion_check=complete,
        max_steps=3,
    )

    assert result.done is False
    assert result.stop_reason == "visual_proof_unavailable"
    assert "Визуальная проверка недоступна" in result.summary
    assert see_calls == 1


@pytest.mark.asyncio
async def test_max_applies_one_actionable_visual_fix_before_local_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "first"})),
            _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"})),
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "polished"})),
            _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"})),
        ]
    )
    calls = 0
    see_calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls == 1,
                "detail": "Apply this concrete visual fix.",
            }
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="t",
        execute=execute,
        completion_check=complete,
        max_steps=10,
    )

    assert result.done is True
    assert result.stop_reason == "contract_green"
    assert result.files["src/app/page.tsx"] == "polished"
    assert calls == 4
    assert see_calls == 2


@pytest.mark.asyncio
async def test_same_turn_write_cannot_claim_it_applied_visual_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = iter(
        [
            _turn(
                ("write_file", {"path": "src/app/page.tsx", "content": "first"}),
                ("build", {}),
                ("runtime_check", {"path": "/"}),
                ("see", {"path": "/"}),
                ("write_file", {"path": "src/app/page.tsx", "content": "same-turn"}),
            ),
            _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"})),
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "later-fix"})),
            _turn(("build", {}), ("runtime_check", {"path": "/"}), ("see", {"path": "/"})),
        ]
    )
    calls = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    see_calls = 0

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal see_calls
        if action.name == "see":
            see_calls += 1
            return {
                "ok": True,
                "needs_fix": see_calls < 3,
                "detail": "Fix the mobile product hierarchy.",
            }
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    def complete(files: Any, evidence: Any) -> str | None:
        required = ("build_after_write", "runtime_check_after_write", "see_after_write")
        return None if files and all(evidence.get(key) for key in required) else "proof missing"

    result = await agent_native.run_native_build(
        system=agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT"),
        task="t",
        execute=execute,
        completion_check=complete,
        max_steps=10,
    )

    assert result.done is True
    assert result.files["src/app/page.tsx"] == "later-fix"
    assert calls == 4


def test_native_api_timeout_outlives_gateway_settlement_window() -> None:
    timeout = agent_native._gateway_timeout()

    assert timeout.connect == 30.0
    assert timeout.write == 60.0
    assert timeout.pool == 30.0
    assert timeout.read == 660.0
