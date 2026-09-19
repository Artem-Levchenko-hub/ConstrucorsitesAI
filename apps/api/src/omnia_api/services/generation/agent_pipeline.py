from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.redis import (
    clear_stream_state,
    publish_event,
)
from omnia_api.services import (
    agent_builder,
    app_errors,
)
from omnia_api.services.generation.agent_finalization import finalize_max_candidate
from omnia_api.services.generation.agent_generation import (
    complete_empty_legacy_build,
    execute_agent_turn,
)
from omnia_api.services.generation.agent_messages import (
    _agent_needs_continue_card,
    _agent_product_failure,
)
from omnia_api.services.generation.agent_preparation import (
    classify_agent_turn,
    prepare_stack_prompt,
)
from omnia_api.services.generation.agent_prompt import prepare_agent_prompt
from omnia_api.services.generation.agent_publication import publish_agent_candidate
from omnia_api.services.generation.agent_recovery import (
    recover_rejected_candidate,
    recover_stopped_candidate,
)
from omnia_api.services.generation.agent_runtime import prepare_agent_runtime, select_nonmax_runtime
from omnia_api.services.generation.agent_seed import render_current_max_starter, stage_max_starter
from omnia_api.services.generation.agent_verification import (
    apply_legacy_design_and_result_text,
    check_backend_and_normalize_css,
    check_coverage_gate,
    check_runtime_security_gates,
    probe_agent_candidate,
    repair_legacy_edit,
)
from omnia_api.services.generation.contracts import (
    AgentOperations,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.progress import GenerationProgress
from omnia_api.services.generation_runs import record_generation_product_failure

_log = logging.getLogger("omnia_api.routers.messages")


async def run_agent_generation(
    *,
    _consume_free_generation: Callable[[AsyncSession], Awaitable[None]],
    _defer_max_runtime_provision: bool,
    _provision_legacy_runtime_with_progress: Callable[[], Awaitable[None]],
    baseline: SourceBaseline,
    capacity_dispatch_token: UUID | None,
    factory: async_sessionmaker[AsyncSession],
    force_model: str | None,
    ids: GenerationIds,
    is_free: bool,
    model_id: str,
    orchestrate: bool,
    progress: GenerationProgress,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    runtime: GenerationRuntime,
    selected_elements: list[dict[str, Any]] | None,
) -> None:
    # Phase 2: container EDITS go through the agent too (read→edit→build→
    # fix), not blind SEARCH/REPLACE — fixes the "точечные правки" pain.
    # First-build/rebuild = full build prompt; a surgical follow-up = a
    # minimal edit prompt with a smaller step budget.
    # CONTINUE («продолжи») on a non-first-build re-enters the BUILD loop to
    # finish a partial app (the agent reads the live container state), so it
    # is neither a from-scratch build nor a 12-step minimal edit.
    # «продолжи» only makes sense when there is a prior build to finish.
    # NB: `is_first_build` lives in the POST handler (post_prompt), NOT in
    # this worker fn — use the in-scope `current_snapshot_id` (None on a
    # brand-new project's first build) so this never NameErrors.
    _agent_res: agent_builder.AgentResult | None = None
    (
        prompt_text,
        _is_continue,
        _is_edit,
        _max_has_generated_snapshot,
    ) = await classify_agent_turn(
        baseline=baseline,
        factory=factory,
        ids=ids,
        orchestrate=orchestrate,
        project_info=project_info,
        prompt_text=prompt_text,
    )

    _agent_emit = progress.emit_agent_event

    # Omnia Design Pro: one pure pre-build classification. Its compact
    # contract reaches the coding agent and its persisted design memory;
    # it never creates an extra generation/acceptance phase.
    _design_contract = None
    if orchestrate and get_settings().use_design_intelligence_plugin:
        try:
            from omnia_api.services.design_plugin import build_design_contract

            _design_contract = build_design_contract(
                project_id=str(ids.project_id),
                project_name=project_info.name,
                template=project_info.template,
                brief=prompt_text,
                preset_id=project_info.design_preset_id,
            )
            if _design_contract:
                print(
                    "[PP] design_plugin "
                    f"version={_design_contract.version} "
                    f"archetype={_design_contract.archetype} "
                    f"preset={_design_contract.preset_id}",
                    flush=True,
                )
        except Exception as _dp_exc:
            print(f"[PP] design_plugin skipped: {_dp_exc!r}", flush=True)

    bindings, _agent_res = await prepare_agent_runtime(
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        progress=progress,
        factory=factory,
        prompt_text=prompt_text,
        _design_contract=_design_contract,
        _agent_res=_agent_res,
        _defer_max_runtime_provision=_defer_max_runtime_provision,
        _provision_legacy_runtime_with_progress=_provision_legacy_runtime_with_progress,
        capacity_dispatch_token=capacity_dispatch_token,
    )
    _stack = await prepare_stack_prompt(
        _design_contract=_design_contract,
        factory=factory,
        ids=ids,
        orchestrate=orchestrate,
        project_info=project_info,
        prompt_text=prompt_text,
        runtime=runtime,
    )
    # Build Plan (эскиз перед стройкой, owner 2026-06-30). On a fresh BUILD
    # run the planner pass → a bounded feature spec (screens/entities/
    # capabilities), persist it in discovery_spec JSONB, and ride its
    # checklist into the agent prompt via _seed_block (the agent builds the
    # WHOLE plan, not a thin green subset). On continue/edit, read the
    # persisted plan back so the same checklist still guides. Fully fail-soft
    # (R-10): empty plan or any error → nothing appended (today's behaviour).
    # Gated by use_build_plan; the coverage gate (P2) verifies it later.
    _prompt_plan, _build_plan = await prepare_agent_prompt(
        stack=_stack,
        factory=factory,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        orchestrate=orchestrate,
        prompt_text=prompt_text,
        selected_elements=selected_elements,
        _is_edit=_is_edit,
        _is_continue=_is_continue,
        force_model=force_model,
    )

    _render_current_max_starter_files = partial(
        render_current_max_starter,
        factory=factory,
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        runtime=runtime,
    )

    _max_seed_files, _agent_res = await stage_max_starter(
        _agent_emit=_agent_emit,
        _agent_res=_agent_res,
        _design_contract=_design_contract,
        _max_has_generated_snapshot=_max_has_generated_snapshot,
        _render_current_max_starter_files=_render_current_max_starter_files,
        bindings=bindings,
        ids=ids,
        orchestrate=orchestrate,
        project_info=project_info,
        runtime=runtime,
    )

    _agent_res = await select_nonmax_runtime(
        _agent_emit=_agent_emit,
        _agent_res=_agent_res,
        bindings=bindings,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
    )

    _operations = AgentOperations(
        execute=bindings.execute,
        emit=_agent_emit,
        probe_runtime=bindings.probe_runtime,
        probe_build=bindings.probe_build,
        preview_url=bindings.preview_url,
    )
    (
        _agent_res,
        _provider_failure,
    ) = await execute_agent_turn(
        _agent_res=_agent_res,
        _is_edit=_is_edit,
        _max_has_generated_snapshot=_max_has_generated_snapshot,
        _max_seed_files=_max_seed_files,
        _max_shell_enabled=bindings.shell_enabled,
        baseline=baseline,
        ids=ids,
        is_free=is_free,
        project_info=project_info,
        prompt_text=prompt_text,
        runtime=runtime,
        plan=_prompt_plan,
        operations=_operations,
    )
    # Rollback is evaluated only after the same-run continuation policy
    # reaches done, cancellation/infra/no-progress, or its runaway backstop.
    # A bounded provider segment is not itself grounds to delete progress.
    (
        _agent_res,
        _first_max_without_product,
        _max_seed_files,
        _seg,
    ) = await recover_stopped_candidate(
        _agent_res=_agent_res,
        _max_has_generated_snapshot=_max_has_generated_snapshot,
        _max_seed_files=_max_seed_files,
        _provider_failure=_provider_failure,
        _render_current_max_starter_files=_render_current_max_starter_files,
        baseline=baseline,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        operations=_operations,
    )
    (
        _agent_res,
        files,
        _total_steps,
    ) = await complete_empty_legacy_build(
        _agent_res=_agent_res,
        _is_edit=_is_edit,
        _max_seed_files=_max_seed_files,
        ids=ids,
        runtime=runtime,
        plan=_prompt_plan,
        operations=_operations,
    )

    # Layer C — backend-guardrail self-heal. Custom server logic is allowed,
    # but raw DB access remains behind the managed boundary until isolation is
    # enforced by Postgres rather than a forgeable source-code string check.
    await check_backend_and_normalize_css(
        _active_max_locked_files=bindings.locked_files,
        _agent_res=_agent_res,
        _is_edit=_is_edit,
        _max_seed_files=_max_seed_files,
        baseline=baseline,
        files=files,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        plan=_prompt_plan,
        operations=_operations,
    )

    # Honest chat: never surface the raw internal summary on a non-done exit
    # (the "hit step budget without calling done" leak). See helper.
    (
        accumulated,
        _runtime_ok,
        _typecheck_ok,
        _rt_error,
        _tc_error,
    ) = await probe_agent_candidate(
        _agent_res=_agent_res,
        _is_edit=_is_edit,
        _seg=_seg,
        _total_steps=_total_steps,
        files=files,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        operations=_operations,
    )

    # ── Edit auto-repair «до талого» ──────────────────────────────────
    # A point-edit that didn't land cleanly — nothing written, a red
    # typecheck, or a 5xx render — used to surface «Не удалось завершить
    # правку — нажми Починить». Owner 2026-06-28: just DO the repair. Re-run
    # the agent on the STRONG model with a forceful «apply the change NOW +
    # here is the concrete error» edit prompt, then re-probe green — up to
    # N times. Bounded + fail-soft (a repair-run exception stops the loop,
    # never breaks the build).
    (
        accumulated,
        _runtime_ok,
        _typecheck_ok,
        _rt_error,
        _tc_error,
    ) = await repair_legacy_edit(
        _is_edit=_is_edit,
        _rt_error=_rt_error,
        _runtime_ok=_runtime_ok,
        _tc_error=_tc_error,
        _typecheck_ok=_typecheck_ok,
        accumulated=accumulated,
        files=files,
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        plan=_prompt_plan,
        operations=_operations,
    )
    # ──────────────────────────────────────────────────────────────────

    # Keep the failed candidate's outcome even if restoring the old tree
    # makes the later runtime/typecheck flags green again.
    (
        _agent_verification_failed,
        _agent_res,
        files,
        _runtime_ok,
        _typecheck_ok,
        accumulated,
    ) = await recover_rejected_candidate(
        _agent_res=_agent_res,
        _first_max_without_product=_first_max_without_product,
        _render_current_max_starter_files=_render_current_max_starter_files,
        _rt_error=_rt_error,
        _runtime_ok=_runtime_ok,
        _tc_error=_tc_error,
        _typecheck_ok=_typecheck_ok,
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        operations=_operations,
    )

    # Design DNA — give this entity/agent app a DISTINCT identity (seeded
    # accent + font pairing) so it stops looking identical to every other
    # one ("дизайн одинаковый"). The agent never writes globals.css (it is
    # baked), so we inject a per-project :root override into the container's
    # globals.css directly. Reuses the curated, WCAG-checked, per-project
    # seeded tokens; touches only safe brand knobs (never the canvas
    # neutrals). Fail-soft: any error leaves the default theme.
    (accumulated,) = await apply_legacy_design_and_result_text(
        _agent_res=_agent_res,
        _is_edit=_is_edit,
        _runtime_ok=_runtime_ok,
        _typecheck_ok=_typecheck_ok,
        accumulated=accumulated,
        files=files,
        ids=ids,
        project_info=project_info,
    )

    # Runtime functional gate — the behavioural "works + does not leak" proof
    # (research finding: this gate was defined+unit-tested but UNWIRED; only
    # the static guardrail ran). Drive the live realtime preview and feed a red
    # verdict back to the agent as a BLOCKING outcome so a broken/leaky feature
    # self-heals BEFORE the snapshot lands — a clean typecheck is exactly what a
    # model hallucinates completion around. Per-stack gate (realtime →
    # functional, drizzle/fullstack → isolation/no-leak), fail-soft.
    # Gate also re-runs on an EDIT that touched the realtime UI surface
    # (chat/room/layout/components/globals) — such an edit can silently
    # re-break two-user live delivery or the membership ACL, and an edit
    # was NEVER gated before (`not _is_edit`), so a working messenger could
    # be degraded with no behavioural re-check. Unrelated edits (0 files or
    # non-UI files only) stay fast — the gate is skipped for them.
    # Which behavioural gate applies to this stack, and whether an EDIT
    # touched the surface that gate guards (a first build always gates):
    #   realtime  → functional_gate (works live + membership ACL holds)
    #   drizzle   → isolation_gate  (no data route leaks rows to anon)
    (
        _att_capture,
        _attestation_stack,
        accumulated,
    ) = await check_runtime_security_gates(
        _is_edit=_is_edit,
        _orch_name=_stack.orchestrator_template,
        accumulated=accumulated,
        files=files,
        ids=ids,
        project_info=project_info,
        plan=_prompt_plan,
        operations=_operations,
    )

    # Coverage gate (тезис 2 — «не на зелёном минимуме»). STACK-AGNOSTIC:
    # when a Build Plan exists, completion means its must-have capabilities
    # actually return their expected status — not just a green build. Reuses
    # the SAME self-heal machinery as the runtime gate (build_fix_instruction
    # → run_agent_build, accept files only if the heal stays green). On
    # exhaustion it ships an HONEST "N из M" card (app_errors incomplete)
    # instead of a silent thin app. Fully fail-soft: a SKIPPED verdict
    # (no preview / nothing provable) or any error → no heal, no card →
    # today's behaviour. A gate must never crash the build.
    (
        _att_capture,
        accumulated,
    ) = await check_coverage_gate(
        _att_capture=_att_capture,
        _build_plan=_build_plan,
        _is_edit=_is_edit,
        _orch_name=_stack.orchestrator_template,
        accumulated=accumulated,
        factory=factory,
        files=files,
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        plan=_prompt_plan,
        operations=_operations,
    )

    (
        _max_finalization_proof,
        files,
        accumulated,
    ) = await finalize_max_candidate(
        _is_edit=_is_edit,
        _max_has_generated_snapshot=_max_has_generated_snapshot,
        _max_shell_enabled=bindings.shell_enabled,
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        ids=ids,
        is_free=is_free,
        prompt_text=prompt_text,
        runtime=runtime,
        plan=_prompt_plan,
        operations=_operations,
    )

    # One verdict for the whole turn: the run status, the chat text and the
    # «Продолжить» card below all follow it.
    _product_failure = _agent_product_failure(
        _agent_res,
        verification_failed=_agent_verification_failed,
        finalization_complete=_max_finalization_proof is not None,
    )
    if get_settings().use_native_agent and _product_failure is not None:
        # Persist before the assistant becomes final. The next-submit
        # admission path and tracked-task finalizer must see the same
        # failure after normal return, rollback, or lease cleanup.
        async with factory() as session:
            await record_generation_product_failure(session, ids.run_id, _product_failure)
            await session.commit()

    # Universal release proof. The specialised realtime/isolation gates
    # above cover only two stacks; every container build (including MAX)
    # must still prove that its FINAL live tree typechecks, serves and has
    # safe transport headers before its exact commit can be deployed.
    if files and get_settings().use_build_attestation:
        from omnia_api.services.release_proof import run_release_proof

        _release_verdict = await run_release_proof(
            ids.project_id,
            project_info.slug,
            proof=_max_finalization_proof,
            require_max_data=project_info.template == "max_miniapp",
            project_cell_handle=runtime.handle,
        )
        if _att_capture is None:
            _att_capture = []
        _att_capture.append(("release", _release_verdict))
        print(
            f"[ATTEST] universal release proof passed={_release_verdict.passed}",
            flush=True,
        )
    await publish_agent_candidate(
        _att_capture=_att_capture,
        _attestation_stack=_attestation_stack,
        _consume_free_generation=_consume_free_generation,
        _max_finalization_proof=_max_finalization_proof,
        _orch_name=_stack.orchestrator_template,
        accumulated=accumulated,
        baseline=baseline,
        factory=factory,
        files=files,
        ids=ids,
        model_id=model_id,
        progress=progress,
        project_info=project_info,
        prompt_text=prompt_text,
        runtime=runtime,
    )

    # Resumable partial build: the agent ran out of step budget without
    # calling done, but any partial files already committed above. Surface
    # a NEUTRAL «Продолжить» card (not an error) — the web UI renders it
    # amber with a button that sends «продолжи», which _is_continue_request
    # routes back into the build loop on the live container. Published AFTER
    # the msg.content=accumulated overwrite above (else it'd be wiped) and
    # BEFORE llm.done so the card both persists and animates in.
    if _agent_needs_continue_card(_agent_res, product_failure=_product_failure):
        await app_errors.publish(
            factory,
            ids.project_id,
            ids.assistant_message_id,
            category="incomplete",
            title="Сборка не завершена",
            detail=(
                "Агент исчерпал лимит шагов, не закончив. Часть уже сохранена "
                "— нажми «Продолжить», доделаю с того же места, не начиная заново."
            ),
            fixable=True,
        )

    await publish_event(
        ids.project_id,
        "llm.done",
        {
            "message_id": str(ids.assistant_message_id),
            "tokens_in": None,
            "tokens_out": None,
            "cost_rub": None,
        },
    )
    await clear_stream_state(ids.project_id, ids.assistant_message_id)
    return
