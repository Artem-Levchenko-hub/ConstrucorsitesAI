from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder
from yleum_api.services.generation.contracts import (
    AgentOperations,
    AgentPromptPlan,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.file_transforms import _merge_seeded_agent_files

_log = logging.getLogger("yleum_api.routers.messages")


def _primary_provider_failure(result: agent_builder.AgentResult) -> str | None:
    if result.stop_reason == "provider_error":
        return result.summary
    if result.stop_reason == "output_limit" and not result.needs_finalization:
        return "Provider response rejected (output_limit); unverified changes were not published."
    return None


async def execute_agent_turn(
    *,
    _agent_res: agent_builder.AgentResult | None,
    _is_edit: bool,
    _max_has_generated_snapshot: bool,
    _max_seed_files: dict[str, str],
    _max_shell_enabled: bool,
    baseline: SourceBaseline,
    ids: GenerationIds,
    is_free: bool,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    runtime: GenerationRuntime,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> tuple[agent_builder.AgentResult, str | None]:
    if _agent_res is None and get_settings().use_native_agent:
        # Native tool-use path (owner «как Claude Code, только на сервере»): one
        # GenerationRun/Project Cell, with bounded fresh provider segments
        # while verified product progress continues. Each segment preserves
        # cumulative files and completion evidence; it never creates another
        # message, run or workspace.
        # fact-gate only (the `build` tool). Reuses the SAME executor; the
        # native system prompt drops the text-action LOOP_PROTOCOL. Handles
        # bare/from-scratch builds too (no forced template). Guards are
        # TURN-level (generous): no-write nudge@6/abort@12 turns + an
        # infra circuit breaker (container dead → abort in ~3 turns) —
        # see agent_native._NO_WRITE_*/_INFRA_DEAD_ABORT_AT.
        from yleum_api.services import agent_native

        _completion_check: Callable[[Mapping[str, str], Mapping[str, int]], str | None] | None = (
            None
        )
        if not _is_edit:
            from yleum_api.services.max_generation_contract import (
                max_completion_gap,
                max_source_completion_gap,
            )

            # A service/config snapshot may predate the first real product
            # and can contain the retired UI canvas. It is not product input
            # and must never satisfy the completion contract.
            _max_baseline_files = {} if not _max_has_generated_snapshot else dict(baseline.files)

            def _max_completion_check(
                written: Mapping[str, str], evidence: Mapping[str, int]
            ) -> str | None:
                effective_files = {
                    **_max_baseline_files,
                    **_max_seed_files,
                    **written,
                }
                if runtime.coordinator is not None:
                    return max_source_completion_gap(
                        prompt_text,
                        effective_files,
                        portable=True,
                    )
                return max_completion_gap(
                    prompt_text,
                    effective_files,
                    evidence,
                    portable=(runtime.handle is not None and runtime.handle.is_portable()),
                )

            _completion_check = _max_completion_check

        # Existing-project follow-ups also include inspection and continuation.
        # Only an explicit leading change command earns a write requirement.
        _change_requested = re.match(
            r"^\s*(?:пожалуйста[,\s]+)?(?:добавь|исправь|измени|поменяй|замени|"
            r"удали|убери|реализуй|fix|add|change|replace|remove|"
            r"delete|implement|update)\b",
            prompt_text,
            flags=re.IGNORECASE,
        ) is not None
        _read_only_requested = re.search(
            r"ничего\s+не\s+(?:меняй|изменяй|трогай)|"
            r"(?:do\s+not|don't)\s+(?:change|modify|edit)\s+(?:anything|files|code)",
            prompt_text,
            flags=re.IGNORECASE,
        ) is not None
        _change_requested = _change_requested and not _read_only_requested
        _native_execute: Callable[[agent_builder.Action], Awaitable[dict[str, Any]]]
        if (_is_edit or _max_has_generated_snapshot) and _change_requested and runtime.handle:
            from yleum_api.services.generation.agent_finalization import (
                validate_edit_source_change,
            )

            _product_completion_check = _completion_check
            _edit_patch: dict[str, str] = {}

            async def _execute_edit_action(action: agent_builder.Action) -> dict[str, Any]:
                nonlocal _edit_patch
                result = await operations.execute(action)
                assert runtime.handle is not None
                # Export is the executor's in-memory diff, including deletion
                # tombstones; model-reported cumulative files are not that diff.
                _edit_patch = await runtime.handle.export_files()
                return result

            def _edit_completion_check(
                written: Mapping[str, str], evidence: Mapping[str, int]
            ) -> str | None:
                # Reject premature done inside the same provider turn, while
                # the agent can still apply the requested change. Passing the
                # existing tree below also prevents edits from being treated
                # as a first build that must rewrite the product entry page.
                verdict = validate_edit_source_change(
                    requires_source_change=True,
                    baseline_files=baseline.files,
                    candidate_files=_edit_patch,
                    exact_tree=False,
                    message="",
                )
                if not _edit_patch or verdict.failure is not None:
                    return (
                        "The requested edit has not changed the source tree. "
                        "Apply the requested code change in this workspace, "
                        "preserve working features, then build and call done."
                    )
                if _product_completion_check is not None:
                    return _product_completion_check(written, evidence)
                return None

            _completion_check = _edit_completion_check
            _native_execute = _execute_edit_action
        else:
            _native_execute = operations.execute

        # Even a model that skips build must receive the same migration contract
        # as final verification while it still has a chance to repair this run.
        from yleum_api.services.generation.agent_runtime import guard_native_source_contract

        _native_execute, _completion_check = await guard_native_source_contract(
            runtime, ids, _native_execute, _completion_check,
        )
        _native_max_segments = get_settings().agent_max_segments if not _is_edit else 1
        _agent_res = await agent_native.run_native_build(
            system=agent_native.native_system_prompt(plan.stack_guide or "", plan.skills),
            task=plan.user,
            execute=_native_execute,
            user_id=str(ids.user_id),
            project_id=str(ids.project_id),
            run_id=str(ids.run_id),
            message_id=str(ids.assistant_message_id),
            free=is_free,
            emit=operations.emit,
            completion_check=_completion_check,
            max_steps=plan.steps,
            max_segments=_native_max_segments,
            allow_max_bash=_max_shell_enabled,
            portable_cell=bool(runtime.handle is not None and runtime.handle.is_portable()),
            initial_files=baseline.files if _is_edit or _max_has_generated_snapshot else None,
            edit_deadline=(
                await runtime.coordinator.source_edit_deadline()
                if runtime.coordinator is not None else None
            ),
        )
    elif _agent_res is None:
        _agent_res = await agent_builder.run_agent_build(
            system_prompt=plan.system,
            user_prompt=plan.user,
            model=plan.model,
            escalate_model=plan.escalate_model,
            execute=operations.execute,
            max_steps=plan.steps,
            emit=operations.emit,
            user_id=str(ids.user_id),
            project_id=str(ids.project_id),
            require_green_before_done=(
                False if plan.bare_stack else get_settings().agent_require_green_before_done
            ),
            ship_green_on_abort=get_settings().agent_ship_green_on_abort,
            edit_mode=_is_edit,
            bare_mode=plan.bare_stack,
        )
    _provider_failure = _primary_provider_failure(_agent_res)
    if (
        _agent_res.stop_reason == "provider_error"
        and _provider_failure
        and not (baseline.sha and _max_has_generated_snapshot)
    ):
        raise RuntimeError(_provider_failure)
    if runtime.handle is not None:
        _agent_res.files = await runtime.handle.export_files()
    # A green starter is not proof that the user's request was generated.
    # Native `done` may otherwise succeed after only reading/building the
    # template. Require at least one attributable model write on a seeded
    # first MAX build; without it, keep the run incomplete instead of
    # committing the untouched starter as a successful generation.
    if _max_seed_files and not _agent_res.files:
        await operations.emit(
            "agent.stalled",
            {
                "step": _agent_res.steps,
                "action": "no_ai_write",
                "human": "AI-агент не внёс изменения — сборка не засчитана",
                "path": "",
                "detail": (
                    "Проверенный шаблон остался без осмысленной AI-правки; "
                    "он не будет выдан как готовый результат."
                ),
                "ok": False,
            },
        )
        _agent_res = agent_builder.AgentResult(
            done=False,
            summary=(
                "Google AI-агент не внёс ни одного изменения в MAX-приложение; "
                "неизменённый шаблон не засчитан как готовая генерация."
            ),
            files={},
            steps=_agent_res.steps,
            transcript=_agent_res.transcript,
            stop_reason="no_ai_write",
            evidence=_agent_res.evidence,
            segments=_agent_res.segments,
        )

    return _agent_res, _provider_failure


async def complete_empty_legacy_build(
    *,
    _agent_res: agent_builder.AgentResult,
    _is_edit: bool,
    _max_seed_files: dict[str, str],
    ids: GenerationIds,
    runtime: GenerationRuntime,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> tuple[agent_builder.AgentResult, dict[str, str], int]:
    _all_files = _merge_seeded_agent_files(_max_seed_files, _agent_res.files)
    _total_steps = _agent_res.steps

    files = _all_files

    # ZERO-FILE FIRST BUILD floor. A first build that wrote NOTHING is the
    # worst case — the templates now ship a WORKING themed default, so the
    # agent can look at it and call `done` having personalised nothing
    # (2026-07-09 «мессенджер2»: files=0 → the app shipped as the raw
    # scaffold on the default indigo, no brand, no feature work). Force ONE
    # escalated retry with an imperative "you MUST write" prompt (mirrors
    # edit_auto_repair, which is edit-only). Not a spin: single pass, strong
    # model, then whatever it produced stands.
    if (
        not _is_edit
        and not files
        and get_settings().use_agentic_builder
        and not get_settings().use_native_agent
        and _agent_res.stop_reason
        not in {
            "max_steps_green",
            "max_steps_rolled_back",
            "provider_stopped_green",
            "provider_stopped_rolled_back",
            "unsafe_changes_rolled_back",
        }
    ):
        print("[PP] agentic_build files=0 → one escalated write-floor retry", flush=True)
        try:
            await operations.emit(
                "agent.step",
                {"action": "оформляю дизайн под задачу…", "kind": "step"},
            )
        except Exception:
            pass
        _floor_res = await agent_builder.run_agent_build(
            system_prompt=plan.stack_system,
            user_prompt=(
                "Ты НИЧЕГО не записал — это НЕ готовая сборка. Шаблон ставит "
                "рабочий, но ДЕФОЛТНЫЙ вид (индиго-акцент, демо-экраны). Твоя "
                "следующая команда ОБЯЗАНА быть write_file/edit_file. Сделай "
                "приложение своим под запрос пользователя:\n"
                "1) Дизайн: перепиши палитру (--primary/--accent) и шрифт в "
                "src/app/globals.css + src/app/layout.tsx под нишу/бренд из "
                "запроса; забрендируй экраны (шапка, сайдбар, бабблы, "
                "auth) — токенами, без neutral-*/#000.\n"
                "2) Функционал: допиши всё, чего требует запрос сверх демо "
                "(экраны, поля, действия).\n"
                f"Затем build, почини до чистоты, проверь и done.{plan.seed_context}"
            ),
            model=plan.escalate_model or plan.model,
            escalate_model=plan.escalate_model,
            execute=operations.execute,
            max_steps=max(int(plan.steps), 24),
            emit=operations.emit,
            user_id=str(ids.user_id),
            project_id=str(ids.project_id),
            require_green_before_done=get_settings().agent_require_green_before_done,
            ship_green_on_abort=get_settings().agent_ship_green_on_abort,
        )
        _total_steps += _floor_res.steps
        if _floor_res.files:
            _all_files.update(_floor_res.files)
            files = _all_files
            _agent_res = _floor_res
        print(
            f"[PP] write-floor retry done files={len(files)} stop={_floor_res.stop_reason}",
            flush=True,
        )
        if runtime.handle is not None:
            _agent_res.files = await runtime.handle.export_files()
            _all_files = _merge_seeded_agent_files(_max_seed_files, _agent_res.files)
            files = _all_files

    if runtime.handle is not None:
        # A retained failed workspace can predate this run's accepted source.
        # export_files is a diff against that workspace, NOT against accepted
        # source or the platform seed. Reusing it here can drop existing SQL
        # from a tree that just passed build/migration checks, or revive deleted
        # files. Final verification must receive that same full checked tree.
        snapshot = await runtime.handle.snapshot_files()
        reference = runtime.migration_baseline or _max_seed_files
        files = {**dict.fromkeys(reference.keys() - snapshot.keys(), ""), **snapshot}

    return _agent_res, files, _total_steps
