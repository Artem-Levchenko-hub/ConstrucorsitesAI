from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from functools import partial
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.core.redis import (
    clear_stream_state,
    publish_event,
)
from yleum_api.services import (
    agent_builder,
    app_errors,
)
from yleum_api.services.generation.agent_finalization import (
    finalize_max_candidate,
    source_change_allows_partial_save,
    validate_edit_source_change,
)
from yleum_api.services.generation.agent_generation import (
    SourceCheckpointError,
    complete_empty_legacy_build,
    execute_agent_turn,
)
from yleum_api.services.generation.agent_messages import (
    _agent_needs_continue_card,
    _agent_product_failure,
)
from yleum_api.services.generation.agent_preparation import (
    classify_agent_turn,
    prepare_stack_prompt,
)
from yleum_api.services.generation.agent_prompt import prepare_agent_prompt
from yleum_api.services.generation.agent_publication import publish_agent_candidate
from yleum_api.services.generation.agent_recovery import (
    recover_rejected_candidate,
    recover_stopped_candidate,
)
from yleum_api.services.generation.agent_runtime import (
    prepare_agent_runtime,
    require_named_behavior_readiness,
)
from yleum_api.services.generation.agent_seed import render_current_max_starter, stage_max_starter
from yleum_api.services.generation.agent_verification import (
    apply_legacy_design_and_result_text,
    check_backend_and_normalize_css,
    check_coverage_gate,
    check_runtime_security_gates,
    probe_agent_candidate,
    repair_legacy_edit,
)
from yleum_api.services.generation.contracts import (
    AgentOperations,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.progress import GenerationProgress
from yleum_api.services.generation.recovery_contract import (
    ContinuationClaim,
    RecoveryContractError,
    RecoveryStateV2,
)
from yleum_api.services.generation.runtime import _apply_project_cell_preview_files
from yleum_api.services.generation.source_checkpoint import (
    SourceCheckpoint,
    adjacent_checkpoint,
    checkpoint_resume_request,
    persist_checkpoint,
)
from yleum_api.services.generation_runs import record_generation_product_failure

_log = logging.getLogger("yleum_api.routers.messages")


async def resume_agent_generation(claim: ContinuationClaim) -> None:
    """Enter the saved logical stage, without admission or original dispatch."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.lifecycle import cleanup_generation_runtime
    from yleum_api.services.generation.publication import consume_free_generation
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        load_execution_context,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_store import _baseline_sha, _locked_owner
    from yleum_api.services.generation_runs import load_generation_dispatch
    from yleum_api.services.llm_client import set_free_generation, set_generation_billing_context

    await _require_session_owner(claim.run_id)
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        hint = await session.get(GenerationRun, claim.run_id)
        if hint is None:
            raise RecoveryContractError("continuation run disappeared")
        run, project = await _locked_owner(
            session, run_id=claim.run_id, project_id=hint.project_id, user_id=hint.user_id,
        )
        state = recovery_state(run)
        if (not isinstance(state, RecoveryStateV2) or state.continuation_claim != claim
            or state.phase != "working" or state.execution.execution_context_ref is None):
            raise RecoveryContractError("continuation cursor is no longer owned")
        from yleum_api.services.generation.agent_publication import (
            has_publication_intent,
            publication_committed,
        )
        if has_publication_intent(state):
            if not await publication_committed(session, run=run, project=project, state=state):
                raise RecoveryContractError(
                    "publication outcome remains unresolved; writer cannot replay"
                )
            await _require_session_owner(run.id)
            return  # Exact SQL terminal receipt; no source preparation or publication replay.
        baseline_sha = await _baseline_sha(session, run=run, project=project, state=state)
        dispatch = load_generation_dispatch(run)
        await session.commit()
    saved = await load_execution_context(
        state.identity, state.execution.execution_context_ref, binding=state.binding,
    )
    if (set(saved) - {"active_tool", "native_outer", "legacy_outer"}
        != {"schema_version", "entry", "pipeline", "completed", "active_frame"}):
        raise RecoveryContractError("unsupported executable pipeline context")
    data = saved["pipeline"]
    keys = {"ids", "baseline", "project_info", "prompt_text", "force_model", "is_free",
            "model_id", "orchestrate", "selected_elements"}
    if (saved["entry"] != "pipeline" or type(data) is not dict or set(data) != keys
        or type(saved["completed"]) is not dict):
        raise RecoveryContractError("invalid executable pipeline shape")

    def record(record_type: Any, raw: Any) -> Any:
        if type(raw) is not dict or set(raw) != set(record_type.__dataclass_fields__):
            raise RecoveryContractError("invalid executable pipeline record")
        return TypeAdapter(record_type).validate_json(json.dumps(raw, allow_nan=False), strict=True)

    ids = record(GenerationIds, data["ids"])
    baseline = record(SourceBaseline, data["baseline"])
    project_info = record(ProjectGenerationFacts, data["project_info"])
    if (ids != GenerationIds(claim.run_id, dispatch.project_id, dispatch.user_id,
                             dispatch.user_message_id, dispatch.assistant_message_id)
        or baseline.snapshot_id != dispatch.current_snapshot_id or baseline.sha != baseline_sha
        or any(data[key] != getattr(dispatch, key) for key in (
            "prompt_text", "force_model", "is_free", "model_id", "orchestrate", "selected_elements",
        ))
        or type(data["is_free"]) is not bool or type(data["orchestrate"]) is not bool):
        raise RecoveryContractError("executable pipeline conflicts with original admission")
    if state.execution.stage in {"agent", "finalization"}:
        from yleum_api.services.generation.recovery_pipeline import (
            CompletedAgentTurn,
            PreparedAgentEntry,
        )
        PreparedAgentEntry.from_json(saved["completed"].get("agent_entry"))
        if state.execution.stage == "finalization":
            CompletedAgentTurn.from_json(saved["completed"].get("agent_turn"))
    elif (state.execution.stage, state.execution.substep) not in {
        ("bootstrap", "runtime_prepare"), ("bootstrap", "agent_entry"),
        ("bootstrap", "stack_prepare"), ("bootstrap", "prompt_prepare"),
        ("bootstrap", "starter_apply"), ("bootstrap", "proof_wait"),
    }:
        raise RecoveryContractError("executable continuation stage is not installed")
    set_free_generation(data["is_free"])
    set_generation_billing_context(ids.run_id, ids.user_id, ids.project_id)
    runtime = GenerationRuntime()
    try:
        await run_agent_generation(
            _consume_free_generation=partial(consume_free_generation, is_free=data["is_free"],
                                             user_id=ids.user_id),
            baseline=baseline, capacity_dispatch_token=None, factory=factory,
            force_model=data["force_model"], ids=ids, is_free=data["is_free"],
            model_id=data["model_id"], orchestrate=data["orchestrate"],
            progress=GenerationProgress(
                factory, ids.run_id, ids.project_id, ids.assistant_message_id,
            ),
            project_info=project_info, prompt_text=data["prompt_text"], runtime=runtime,
            selected_elements=data["selected_elements"], _resume_state=state,
        )
    finally:
        await cleanup_generation_runtime(runtime, ids.project_id, ids.assistant_message_id)


async def publish_finalized_candidate_once(
    *,
    activation_consumed: bool,
    publish: Callable[[], Awaitable[None]],
) -> bool:
    """Do not create a second snapshot or settle quota after adaptive activation."""
    if activation_consumed:
        return False
    await publish()
    return True


async def run_agent_generation(
    *,
    _consume_free_generation: Callable[[AsyncSession], Awaitable[None]],
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
    _resume_state: RecoveryStateV2 | None = None,
) -> None:
    from yleum_api.services.generation.recovery_requirements import freeze_for_run

    if _resume_state is None:
        async with factory() as scope_session:
            await freeze_for_run(
                scope_session, run_id=ids.run_id, project_id=ids.project_id,
                user_id=ids.user_id, prompt=prompt_text, template=project_info.template,
            )
    from yleum_api.services.generation.recovery_execution import initialize_execution

    if _resume_state is None:
        _resume_state = await initialize_execution(factory, run_id=ids.run_id, context=lambda: {
        "schema_version": 1, "entry": "pipeline",
        "pipeline": {
            "ids": {key: str(value) for key, value in asdict(ids).items()},
            "baseline": {"snapshot_id": str(baseline.snapshot_id) if baseline.snapshot_id else None,
                         "sha": baseline.sha, "files": baseline.files},
            "project_info": asdict(project_info), "prompt_text": prompt_text,
            "force_model": force_model, "is_free": is_free, "model_id": model_id,
            "orchestrate": orchestrate, "selected_elements": selected_elements,
        },
        "completed": {}, "active_frame": None,
        })
    from yleum_api.services.generation.recovery_execution import load_execution_context
    from yleum_api.services.generation.recovery_pipeline import (
        BootstrapPreparation,
        CompletedAgentTurn,
        FinalizationInputs,
        PreparedAgentEntry,
        attach_driver,
        complete_agent_turn,
        rebind_continuation,
        run_bootstrap_stage,
        save_finalization_inputs,
    )

    execution_context = None
    prepared_entry = None
    bootstrap_preparation = None
    completed_turn = None
    finalization_inputs = None
    continuation = None
    if _resume_state is not None:
        if _resume_state.execution.execution_context_ref is None:
            raise RecoveryContractError("pipeline executable context is missing")
        execution_context = await load_execution_context(
            _resume_state.identity, _resume_state.execution.execution_context_ref,
            binding=_resume_state.binding,
        )
        completed = execution_context.get("completed")
        if type(completed) is not dict:
            raise RecoveryContractError("pipeline preparation inventory is missing")
        if "bootstrap_preparation" in completed:
            bootstrap_preparation = BootstrapPreparation.model_validate_json(
                json.dumps(completed["bootstrap_preparation"], allow_nan=False),
            )
        if "agent_entry" in completed:
            prepared_entry = PreparedAgentEntry.from_json(completed["agent_entry"])
        if "agent_turn" in completed:
            if _resume_state.execution.stage != "finalization":
                raise RecoveryContractError(
                    "completed agent turn has a conflicting execution cursor"
                )
            completed_turn = CompletedAgentTurn.from_json(completed["agent_turn"])
            runtime.response_recovery.budget = completed_turn.response_budget
            if "response_budget" in completed:
                from yleum_api.services.generation.recovery_contract import IncompleteResponseBudget
                shared_budget = IncompleteResponseBudget.model_validate_json(
                    json.dumps(completed["response_budget"], allow_nan=False),
                )
                if shared_budget.total < completed_turn.response_budget.total:
                    raise RecoveryContractError("finalization response accounting was refunded")
                runtime.response_recovery.budget = shared_budget
        if "finalization_inputs" in completed:
            if completed_turn is None:
                raise RecoveryContractError("finalization inputs have no completed main turn")
            finalization_inputs = FinalizationInputs.from_json(completed["finalization_inputs"])

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
    resumed: tuple[SourceCheckpoint, str] | None = None
    if prepared_entry is not None:
        (prompt_text, _is_continue, _is_edit,
         _max_has_generated_snapshot) = prepared_entry.classification
    elif bootstrap_preparation is not None:
        (prompt_text, _is_continue, _is_edit,
         _max_has_generated_snapshot) = bootstrap_preparation.classification
        if bootstrap_preparation.adjacent_files is not None:
            resumed = ({
                "files": bootstrap_preparation.adjacent_files,
                "deleted_paths": list(bootstrap_preparation.adjacent_deleted_paths),
            }, prompt_text)
    else:
        if project_info.template == "max_miniapp" and checkpoint_resume_request(prompt_text):
            resumed = await adjacent_checkpoint(factory=factory, ids=ids, baseline=baseline)
            if resumed is not None:
                prompt_text = resumed[1]
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

    # Yleum Design Pro: one pure pre-build classification. Its compact
    # contract reaches the coding agent and its persisted design memory;
    # it never creates an extra generation/acceptance phase.
    _design_contract = (
        prepared_entry.design_contract if prepared_entry is not None
        else bootstrap_preparation.design_contract if bootstrap_preparation is not None
        else None
    )
    if (prepared_entry is None and bootstrap_preparation is None and orchestrate
        and get_settings().use_design_intelligence_plugin):
        try:
            from yleum_api.services.design_plugin import build_design_contract

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

    if _resume_state is not None and bootstrap_preparation is None and prepared_entry is None:
        from yleum_api.services.generation.recovery_execution import commit_execution_cursor

        assert execution_context is not None
        bootstrap_preparation = BootstrapPreparation(
            classification=(prompt_text, _is_continue, _is_edit, _max_has_generated_snapshot),
            design_contract=_design_contract,
            adjacent_files=resumed[0]["files"] if resumed is not None else None,
            adjacent_deleted_paths=tuple(resumed[0]["deleted_paths"]) if resumed else (),
        )
        execution_context = {**execution_context, "completed": {
            **execution_context["completed"],
            "bootstrap_preparation": bootstrap_preparation.model_dump(mode="json"),
        }}
        _resume_state = await commit_execution_cursor(
            factory, previous=_resume_state, execution=_resume_state.execution,
            context=execution_context,
        )

    bindings, _agent_res = await prepare_agent_runtime(
        ids=ids,
        project_info=project_info,
        runtime=runtime,
        progress=progress,
        factory=factory,
        prompt_text=prompt_text,
        _design_contract=_design_contract,
        _agent_res=_agent_res,
        capacity_dispatch_token=capacity_dispatch_token,
    )
    if _resume_state is not None:
        assert execution_context is not None
        continuation = attach_driver(factory, _resume_state, execution_context, runtime.handle)
        runtime.continuation = continuation
        assert runtime.handle is not None
        await rebind_continuation(continuation)
        if continuation.state.execution.physical_state == "no_effects":
            await continuation.save(continuation.state.execution.model_copy(update={
                "physical_state": "active",
                "control_workspace_id": runtime.handle.control_workspace_id,
                "control_fencing_epoch": runtime.handle.control_fencing_epoch,
            }), context=continuation.context)
        if runtime.coordinator is not None:
            runtime.coordinator.continuation = continuation
        if runtime.handle is not None:
            from yleum_api.services.generation.recovery_pipeline import reconcile_pending_role
            await reconcile_pending_role(continuation, runtime.handle)

    await require_named_behavior_readiness(
        runtime, prompt_text, template=project_info.template, factory=factory, ids=ids,
    )
    if prepared_entry is not None:
        _stack = prepared_entry.stack
    else:
        _stack = await run_bootstrap_stage(
            continuation, "stack_prepare", lambda: prepare_stack_prompt(
            _design_contract=_design_contract,
            factory=factory,
            ids=ids,
            orchestrate=orchestrate,
            project_info=project_info,
            prompt_text=prompt_text,
            runtime=runtime,
        ), handle=runtime.handle)
    # The prepared prompt is executable data. A cold continuation reuses it
    # exactly; current runtime/secret/proof authority is rebuilt independently.
    if prepared_entry is not None:
        _prompt_plan, _build_plan = prepared_entry.prompt_plan, None
    else:
        _prompt_plan, _build_plan = await run_bootstrap_stage(
            continuation, "prompt_prepare", lambda: prepare_agent_prompt(
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
        ), handle=runtime.handle)

    _render_current_max_starter_files = partial(
        render_current_max_starter,
        factory=factory,
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        runtime=runtime,
    )

    if prepared_entry is not None:
        _max_seed_files, _agent_res = prepared_entry.seed_files, prepared_entry.initial_result
    else:
        _max_seed_files, _agent_res = await run_bootstrap_stage(
            continuation, "starter_apply", lambda: stage_max_starter(
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
        ), handle=runtime.handle)

    runtime.migration_baseline = {**baseline.files, **_max_seed_files}
    if resumed is not None and runtime.handle is not None:
        saved_source = resumed[0]
        source_handle = runtime.handle
        await run_bootstrap_stage(
            continuation, "adjacent_apply", lambda: _apply_project_cell_preview_files(
            files={**saved_source["files"], **dict.fromkeys(saved_source["deleted_paths"], "")},
            empty_files=tuple(
                path for path, content in saved_source["files"].items() if not content
            ),
            project_cell_handle=source_handle, sync_preview=False,
        ), handle=runtime.handle)
        await _agent_emit("agent.continuation", {
            "human": "Восстанавливаю сохранённый черновик и продолжаю проверку", "ok": None,
        })
    edit_deadline = (
        prepared_entry.edit_deadline if prepared_entry is not None else
        (await runtime.coordinator.source_edit_deadline()
         if runtime.coordinator is not None else None)
    )
    if continuation is not None:
        edit_deadline = continuation.soft_edit_deadline(edit_deadline)
    if _resume_state is not None:
        assert execution_context is not None
        assert continuation is not None
        if prepared_entry is None:
            assert runtime.handle is not None
            if _build_plan is not None:
                raise RecoveryContractError("unsupported executable build plan version")
            prepared_entry = PreparedAgentEntry(
                (prompt_text, _is_continue, _is_edit, _max_has_generated_snapshot),
                _design_contract, _stack, _prompt_plan, _max_seed_files, _agent_res, edit_deadline,
            )
            await continuation.save(continuation.state.execution.model_copy(update={
                "substep": "agent_entry", "physical_state": "active",
                "control_workspace_id": runtime.handle.control_workspace_id,
                "control_fencing_epoch": runtime.handle.control_fencing_epoch,
            }), context={**continuation.context, "completed": {
                **continuation.context["completed"], "agent_entry": prepared_entry.to_json(),
            }})
    progress_collector = getattr(runtime, "verified_progress", None)
    _operations = AgentOperations(
        response_recovery=getattr(runtime, "response_recovery", None),
        take_verified_progress=(
            progress_collector.take if progress_collector else None
        ),
        snapshot_files=getattr(runtime.handle, "snapshot_files", None),
        edit_deadline=edit_deadline,
        continuation=continuation,
        execute=bindings.execute,
        emit=_agent_emit,
        probe_runtime=bindings.probe_runtime,
        probe_build=bindings.probe_build,
        preview_url=bindings.preview_url,
    )

    _checkpoint_source_files: dict[str, str] = {}

    async def save_source_checkpoint(result: agent_builder.AgentResult) -> None:
        # Keep all known paths even if the next snapshot or private save fails.
        _checkpoint_source_files.update(result.files)
        if project_info.template != "max_miniapp" or _operations.snapshot_files is None:
            return
        candidate_files = await _operations.snapshot_files()
        _checkpoint_source_files.update(candidate_files)
        if candidate_files == runtime.migration_baseline:
            return
        await persist_checkpoint(
            factory=factory, ids=ids, baseline=baseline, files=candidate_files,
            original_prompt=prompt_text,
        )
        await _agent_emit("agent.checkpoint", {
            "step": result.steps, "human": "Сохраняю черновик, чтобы работа не потерялась",
            "ok": True,
        })
    _operations = replace(_operations, checkpoint=save_source_checkpoint)
    if completed_turn is not None:
        _agent_res, _provider_failure = completed_turn.result, completed_turn.provider_failure
    else:
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
            source_checkpoint=save_source_checkpoint,
            workspace_context_files=(
                await _operations.snapshot_files()
                if resumed is not None and _operations.snapshot_files is not None else None
            ),
        )
        if continuation is not None:
            await complete_agent_turn(
                continuation, _agent_res, _provider_failure, runtime.response_recovery.budget,
            )
    async def _legacy_source_operation(operation: Awaitable[Any]) -> Any:
        try:
            return await operation
        except SourceCheckpointError as exc:
            # Use the same accepted-source restoration as a stopped main turn.
            # A failed private save cannot continue to another heal or publication.
            # A heal result contains only its own changes; include earlier writes,
            # new paths and baseline paths deleted during this run without rereading.
            exc.candidate.files = {
                **baseline.files,
                **(_agent_res.files if _agent_res is not None else {}),
                **_checkpoint_source_files,
                **exc.candidate.files,
            }
            await recover_stopped_candidate(
                _agent_res=exc.candidate,
                _max_has_generated_snapshot=_max_has_generated_snapshot,
                _max_seed_files=_max_seed_files,
                _provider_failure=None,
                _render_current_max_starter_files=_render_current_max_starter_files,
                baseline=baseline, ids=ids, project_info=project_info, runtime=runtime,
                operations=_operations,
            )
            raise RuntimeError(exc.candidate.summary) from None

    if finalization_inputs is None:
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
        ) = await _legacy_source_operation(complete_empty_legacy_build(
            _agent_res=_agent_res,
            _is_edit=_is_edit,
            _max_seed_files=_max_seed_files,
            ids=ids,
            runtime=runtime,
            plan=_prompt_plan,
            operations=_operations,
        ))

        # Layer C — backend-guardrail self-heal. Custom server logic is allowed,
        # but raw DB access remains behind the managed boundary until isolation is
        # enforced by Postgres rather than a forgeable source-code string check.
        await _legacy_source_operation(check_backend_and_normalize_css(
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
        ))

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
        ) = await _legacy_source_operation(repair_legacy_edit(
            coordinator_handoff=runtime.legacy_coordinator_handoff(),
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
        ))
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
            plan=_prompt_plan, prompt_text=prompt_text, is_free=is_free,
            _max_shell_enabled=bindings.shell_enabled,
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


        if continuation is not None:
            await save_finalization_inputs(continuation, FinalizationInputs(
                _agent_res, files, accumulated, _agent_verification_failed,
            ))
    else:
        _agent_res = finalization_inputs.result
        files = dict(finalization_inputs.files)
        accumulated = finalization_inputs.accumulated
        _agent_verification_failed = finalization_inputs.verification_failed

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
    ) = await _legacy_source_operation(check_runtime_security_gates(
        _is_edit=_is_edit,
        _orch_name=_stack.orchestrator_template,
        accumulated=accumulated,
        files=files,
        ids=ids,
        project_info=project_info,
        plan=_prompt_plan,
        operations=_operations,
    ))

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
    ) = await _legacy_source_operation(check_coverage_gate(
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
    ))

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
    from yleum_api.services.restorations import adaptation_activation_consumed

    _activation_consumed_publication = await adaptation_activation_consumed(
        factory, ids.run_id
    )
    _source_change = validate_edit_source_change(
        requires_source_change=_is_edit or _max_has_generated_snapshot,
        baseline_files=baseline.files,
        candidate_files=files,
        exact_tree=_max_finalization_proof is not None,
        message=accumulated,
    )
    files = _source_change.files
    accumulated = _source_change.message
    if _source_change.failure is not None:
        # The proof describes a healthy runtime, but it cannot prove that the
        # requested edit happened. Do not let it authorize an identical tree.
        _max_finalization_proof = None
    _promotion_permit = None
    if _max_finalization_proof is not None and not _activation_consumed_publication:
        from yleum_api.services.promotion_permit import require_promotion_permit

        _promotion_permit = require_promotion_permit(
            _max_finalization_proof.permit,
            proof=_max_finalization_proof,
        )

    # One verdict for the whole turn: the run status, the chat text and the
    # «Продолжить» card below all follow it.
    _product_failure = _source_change.failure or _agent_product_failure(
        _agent_res,
        verification_failed=_agent_verification_failed,
        finalization_complete=_max_finalization_proof is not None,
    )
    _terminal_error: str | None = None
    if _product_failure is not None and (
        _source_change.failure is not None or get_settings().use_native_agent
    ):
        # Persist before the assistant becomes final. The next-submit
        # admission path and tracked-task finalizer must see the same
        # failure after normal return, rollback, or lease cleanup.
        async with factory() as session:
            await record_generation_product_failure(session, ids.run_id, _product_failure)
            await session.commit()
        # The public terminal event must agree with the durable verdict. Only
        # the source-change guard supplies trusted user text; other stage
        # summaries may contain provider diagnostics and must not be exposed.
        _terminal_error = (
            accumulated
            if _source_change.failure is not None
            else "Не удалось завершить изменения. Повтори запрос или уточни, что нужно изменить."
        )

    # Universal release proof. The specialised realtime/isolation gates
    # above cover only two stacks; every container build (including MAX)
    # must still prove that its FINAL live tree typechecks, serves and has
    # safe transport headers before its exact commit can be deployed.
    if files and get_settings().use_build_attestation:
        if _max_finalization_proof is not None:
            from yleum_api.services.max_finalization import proof_bundle_verdict

            _release_verdict = proof_bundle_verdict(
                _max_finalization_proof,
                require_max_data=False,
            )
        else:
            from yleum_api.services.generation.runtime import _require_project_cell
            from yleum_api.services.release_proof import run_release_proof

            _release_verdict = await run_release_proof(
                ids.project_id,
                project_info.slug,
                require_max_data=True,
                project_cell_handle=_require_project_cell(runtime.handle),
            )
        if _att_capture is None:
            _att_capture = []
        _att_capture.append(("release", _release_verdict))
        print(
            f"[ATTEST] universal release proof passed={_release_verdict.passed}",
            flush=True,
        )
    async def _publish_candidate() -> None:
        await publish_agent_candidate(
            _att_capture=_att_capture,
            _attestation_stack=_attestation_stack,
            _consume_free_generation=_consume_free_generation,
            _max_finalization_proof=_max_finalization_proof,
            _promotion_permit=_promotion_permit,
            continuation=continuation,
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

    await publish_finalized_candidate_once(
        activation_consumed=_activation_consumed_publication,
        publish=_publish_candidate,
    )

    # Resumable partial build: the agent ran out of step budget without
    # calling done, but any partial files already committed above. Surface
    # a NEUTRAL «Продолжить» card (not an error) — the web UI renders it
    # amber with a button that sends «продолжи», which _is_continue_request
    # routes back into the build loop on the live container. Published AFTER
    # the msg.content=accumulated overwrite above (else it'd be wiped) and
    # BEFORE llm.done so the card both persists and animates in.
    if source_change_allows_partial_save(_source_change) and _agent_needs_continue_card(
        _agent_res, product_failure=_product_failure
    ):
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

    if _terminal_error is not None:
        await publish_event(
            ids.project_id,
            "llm.error",
            {"message_id": str(ids.assistant_message_id), "error": _terminal_error},
        )
    else:
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
