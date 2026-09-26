from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.services import (
    agent_builder,
    orchestrator_client,
)
from yleum_api.services.generation.agent_messages import (
    _agent_result_message,
    _capture_hard_coverage_attestation,
    summarize_check_failure,
)
from yleum_api.services.generation.contracts import (
    AgentOperations,
    AgentPromptPlan,
    CandidateProbeResult,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.runtime import (
    _abort_unsafe_max_backend,
    _apply_project_cell_preview_files,
    _require_project_cell,
)
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error

_log = logging.getLogger("yleum_api.routers.messages")


async def check_backend_and_normalize_css(
    *,
    _active_max_locked_files: frozenset[str],
    _agent_res: agent_builder.AgentResult,
    _is_edit: bool,
    _max_seed_files: dict[str, str],
    baseline: SourceBaseline,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> None:
    try:
        from yleum_api.services import agent_gate_feedback as _agf
        from yleum_api.services.backend_guardrail import (
            GuardrailVerdict,
            Violation,
        )
        from yleum_api.services.backend_guardrail import check_backend as _check_backend

        def _guard_view() -> dict[str, str]:
            if project_info.template != "max_miniapp":
                return files

            return {
                path: content
                for path, content in {**baseline.files, **files}.items()
                if path not in _active_max_locked_files
            }

        def _backend_verdict() -> GuardrailVerdict:
            if runtime.handle is not None and runtime.handle.is_portable():
                return GuardrailVerdict(
                    safe=True,
                    violations=[],
                    summary="Project data is isolated from managed MAX credentials",
                )
            if project_info.template == "max_miniapp":
                from yleum_api.services.max_generation_contract import unsafe_max_backend_paths

                unsafe = unsafe_max_backend_paths(_guard_view())
                violations = [
                    Violation(
                        path=path,
                        rule="raw product DB access is not isolated",
                        detail=(
                            "use createMaxAction/getMaxActions from the managed integration client"
                        ),
                    )
                    for path in unsafe
                ]
                return GuardrailVerdict(
                    safe=not violations,
                    violations=violations,
                    summary=(
                        "MAX managed persistence boundary OK"
                        if not violations
                        else "MAX managed persistence boundary failed"
                    ),
                )
            return _check_backend(_guard_view())

        _guard_attempt = 0
        _guard_max = max(0, int(get_settings().agent_gate_max_attempts))
        while (
            get_settings().use_agent_gate_feedback
            and not get_settings().use_native_agent
            and not _is_edit
        ):
            _gv = _backend_verdict()
            _outcomes = [
                _agf.GateOutcome(
                    name="backend_guardrail",
                    passed=_gv.safe,
                    failures=[f"{v.path}: {v.rule}" for v in _gv.violations],
                )
            ]
            # SAST gate (K3a) — static injection/secret scan; blocking only
            # when sast_gate_blocking is on (else advisory-logged below).
            if get_settings().use_sast_gate:
                from yleum_api.services.sast_gate import check_sast as _check_sast

                _sv = _check_sast(_guard_view())
                _outcomes.append(
                    _agf.GateOutcome(
                        name="sast",
                        passed=_sv.safe,
                        failures=[f"{f.path}: {f.cwe} {f.rule}" for f in _sv.findings],
                        blocking=get_settings().sast_gate_blocking,
                    )
                )
            _instr = _agf.build_fix_instruction(_outcomes, _guard_attempt, _guard_max)
            if _instr is None:
                break  # clean, or out of retry budget
            _guard_attempt += 1
            print(
                f"[PP] agent_gate_feedback heal attempt={_guard_attempt} "
                f"violations={len(_gv.violations)}",
                flush=True,
            )
            _heal = await agent_builder.run_agent_build(
                system_prompt=plan.system,
                user_prompt=_instr + plan.seed_context,
                model=plan.escalate_model or plan.model,
                execute=operations.execute,
                max_steps=plan.steps,
                emit=operations.emit,
                user_id=str(ids.user_id),
                project_id=str(ids.project_id),
            )
            files.update(_heal.files)
        if project_info.template == "max_miniapp":
            from yleum_api.services.max_data_evolution import max_migration_contract_errors

            _migration_baseline = {**baseline.files, **_max_seed_files}
            _migration_candidate = dict(_migration_baseline)
            for _path, _content in files.items():
                if _content == "":
                    _migration_candidate.pop(_path, None)
                else:
                    _migration_candidate[_path] = _content
            _migration_errors = max_migration_contract_errors(
                _migration_baseline,
                _migration_candidate,
            )
            if _migration_errors:
                await _abort_unsafe_max_backend(
                    project_id=ids.project_id,
                    project_slug=project_info.slug,
                    current_files=_migration_baseline,
                    files=files,
                    unsafe_paths=_migration_errors,
                    project_cell_handle=_require_project_cell(runtime.handle),
                    violation_kind="MAX migration contract",
                )
        # Advisory log regardless of the heal flag — operators SEE a raw-DB
        # escape even when self-heal is off (the silent-failure guard).
        _final_guard = _backend_verdict()
        if not _final_guard.safe:
            print(
                f"[PP] backend_guardrail VIOLATIONS: {_final_guard.summary}",
                flush=True,
            )
            if project_info.template == "max_miniapp":
                await _abort_unsafe_max_backend(
                    project_id=ids.project_id,
                    project_slug=project_info.slug,
                    # A first MAX build seeds a verified platform core
                    # before model writes begin.  Treat that core as part
                    # of the safe baseline so a rejected product draft
                    # restores the core instead of deleting it.
                    current_files={**baseline.files, **_max_seed_files},
                    files=files,
                    unsafe_paths=[violation.path for violation in _final_guard.violations],
                    project_cell_handle=_require_project_cell(runtime.handle),
                )
        # SAST advisory log — operators SEE injection/secret findings even
        # when blocking/heal is off (runs regardless of the feedback loop).
        if get_settings().use_sast_gate:
            from yleum_api.services.sast_gate import check_sast as _check_sast2

            _final_sast = _check_sast2(_guard_view())
            if not _final_sast.safe:
                print(
                    f"[PP] sast_gate FINDINGS: {_final_sast.summary}",
                    flush=True,
                )
    except ApiError:
        raise
    except Exception as _guard_exc:  # never let advisory checks break a build
        raise_if_terminal_cell_error(_guard_exc)
        print(f"[PP] agent_gate_feedback skipped: {_guard_exc!r}", flush=True)

    # Tailwind expands its @import into hundreds of CSS rules. A model
    # that puts Google Fonts immediately after it passes tsc but fails
    # in Turbopack with "@import rules must precede all rules". Repair
    # only import placement before the independent runtime gate; product
    # styles remain entirely model-owned and no extra model call is used.
    if project_info.template == "max_miniapp" and "src/app/globals.css" in files:
        try:
            from yleum_api.services.max_generation_contract import normalize_max_globals_css

            _original_max_css = files["src/app/globals.css"]
            _normalized_max_css = normalize_max_globals_css(_original_max_css)
            if _normalized_max_css != _original_max_css:
                files["src/app/globals.css"] = _normalized_max_css
                await _apply_project_cell_preview_files(
                    files={"src/app/globals.css": _normalized_max_css},
                    project_cell_handle=_require_project_cell(runtime.handle),
                )
                await operations.emit(
                    "agent.step",
                    {
                        "step": _agent_res.steps,
                        "action": "css_safety",
                        "human": "Проверяю порядок CSS-импортов",
                        "path": "src/app/globals.css",
                        "detail": (
                            "Внешние шрифты перенесены перед Tailwind, чтобы "
                            "финальная Turbopack-сборка не упала после clean typecheck."
                        ),
                        "ok": True,
                    },
                )
                print("[PP] MAX CSS imports normalized before runtime", flush=True)
        except Exception as _css_safety_exc:
            raise_if_terminal_cell_error(_css_safety_exc)
            print(f"[PP] MAX CSS normalization skipped: {_css_safety_exc!r}", flush=True)


async def probe_agent_candidate(
    *,
    _agent_res: agent_builder.AgentResult,
    _is_edit: bool,
    _seg: int,
    _total_steps: int,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    operations: AgentOperations,
) -> CandidateProbeResult:
    accumulated = _agent_result_message(_agent_res, is_edit=_is_edit)
    print(
        f"[PP] agentic_build done={_agent_res.done} segs={_seg} "
        f"total_steps={_total_steps} files={len(files)} stop={_agent_res.stop_reason}",
        flush=True,
    )

    # Phase 3 — runtime smoke gate: a clean typecheck does NOT prove the
    # app actually serves. Probe the live container for a 5xx render and
    # surface it honestly instead of shipping a broken app as «готово».
    # Deterministic (one HTTP probe), fail-soft.
    _runtime_ok = True  # fail-soft default: a probe error ≠ a broken app
    _rt_error = ""
    try:
        _rt = (
            {
                "ok": True,
                "detail": "runtime proof reserved for deterministic finalization",
            }
            if runtime.coordinator is not None
            else await operations.probe_runtime("/")
        )
        if runtime.coordinator is None and project_info.template == "max_miniapp" and _rt.get("ok"):
            # A first request can still hit the previous Turbopack graph
            # while HMR notices the last write. Require a second green
            # response after a short settle window before publishing.
            await asyncio.sleep(2)
            _rt = await operations.probe_runtime("/")
        _runtime_ok = bool(_rt.get("ok"))
        if not _runtime_ok:
            _rt_err = _rt.get("error") or _rt.get("status_code") or "5xx"
            _rt_error = str(_rt_err)
            accumulated += (
                f"\n\n⚠️ Рантайм-проверка: приложение отвечает ошибкой "
                f"({_rt_err}). Открой превью и нажми «Починить» или уточни запрос."
            )
            print(f"[PP] agentic_smoke runtime FAIL {_rt.get('status_code')}", flush=True)
        else:
            print("[PP] agentic_smoke runtime ok", flush=True)

            # Fire-and-forget route pre-warm: `next dev` compiles each
            # route lazily on FIRST hit (~30-90s cold), so a reviewer eats
            # that per page on a demo. Force those first hits now, in the
            # background, so the pages are WARM when they land. Best-effort
            # — never blocks the response, never fails the build.
            if runtime.handle is None:

                async def _warm_bg() -> None:
                    try:
                        _w = await orchestrator_client.warm_routes(
                            ids.project_id, project_info.slug
                        )
                        print(f"[PP] warm routes {_w}", flush=True)
                    except Exception as _w_exc:
                        print(f"[PP] warm skipped: {_w_exc!r}", flush=True)

                _warm_task = asyncio.create_task(_warm_bg())
                # Hold a ref so the task isn't GC'd mid-flight; discard on done.
                _warm_task.add_done_callback(lambda _t: None)
    except Exception as _sm_exc:
        raise_if_terminal_cell_error(_sm_exc)
        print(f"[PP] agentic_smoke skipped: {_sm_exc!r}", flush=True)

    # Honesty gate: a page that SERVES can still be typecheck-RED — a client
    # TS error (e.g. TS2739 «missing columns, fields») is NOT a 5xx, so the
    # smoke probe is green while the app is broken. The «looped-but-serves»
    # override below must NOT report «Готово» on a red typecheck (the
    # "делает вид что работает" lie). Run one real typecheck; on a DEFINITE
    # red, surface the first error honestly + keep the fixable card. Fail-soft:
    # an agent_build exception leaves _typecheck_ok=True (unknown ≠ broken).
    _typecheck_ok = True
    _tc_error = ""
    try:
        _tc = await operations.probe_build()
        _typecheck_ok = bool(_tc.get("ok", True))
        if not _typecheck_ok:
            _tc_detail = str(_tc.get("detail") or "").strip()
            _tc_first = (
                summarize_check_failure(_tc_detail) if _tc_detail else "ошибка типизации"
            )
            _tc_error = _tc_first
            accumulated += (
                f"\n\n⚠️ Почти готово, но осталась ошибка: {_tc_first}. "
                f"Нажми «Починить» — доведу до чистоты."
            )
            print(f"[PP] agentic_typecheck RED: {_tc_first}", flush=True)
        else:
            print("[PP] agentic_typecheck clean", flush=True)
    except Exception as _tc_exc:
        raise_if_terminal_cell_error(_tc_exc)
        print(f"[PP] agentic_typecheck skipped: {_tc_exc!r}", flush=True)

    return CandidateProbeResult(accumulated, _runtime_ok, _typecheck_ok, _rt_error, _tc_error)


async def repair_legacy_edit(
    *,
    _is_edit: bool,
    _rt_error: str,
    _runtime_ok: bool,
    _tc_error: str,
    _typecheck_ok: bool,
    accumulated: str,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> CandidateProbeResult:
    if (
        _is_edit
        and get_settings().use_edit_auto_repair
        and not get_settings().use_native_agent
        and (not files or not _typecheck_ok or not _runtime_ok)
    ):
        _ar_max = max(0, int(get_settings().edit_auto_repair_attempts))
        _ar = 0
        while _ar < _ar_max and (not files or not _typecheck_ok or not _runtime_ok):
            _ar += 1
            _why = (
                f"осталась ошибка типизации: {_tc_error}"
                if not _typecheck_ok
                else f"приложение отвечает ошибкой ({_rt_error})"
                if not _runtime_ok
                else "ни один файл не был изменён — правка не применилась"
            )
            _repair_user = (
                "Предыдущая попытка НЕ применила правку до конца. Запрос "
                f"пользователя:\n\n{prompt_text}\n\n"
                f"Что не так ПРЯМО СЕЙЧАС: {_why}.\n\n"
                "Найди нужный файл (grep/read — максимум 1-2 чтения), затем "
                "СРАЗУ внеси правку через edit_file/write_file (твоя следующая "
                "запись ОБЯЗАТЕЛЬНА — не делай ещё один read), запусти build, "
                "почини ошибки до чистоты, затем done. Не переписывай "
                f"работающее.{plan.seed_context}"
            )
            print(
                f"[PP] edit_auto_repair attempt={_ar}/{_ar_max} why={_why[:90]}",
                flush=True,
            )
            try:
                await operations.emit(
                    "agent.step",
                    {
                        "action": f"чиню правку до конца ({_ar}/{_ar_max})…",
                        "kind": "step",
                    },
                )
            except Exception:
                pass
            try:
                _rep = await agent_builder.run_agent_build(
                    system_prompt=(
                        agent_builder.build_edit_system_prompt(plan.stack_guide)
                        if project_info.template == "max_miniapp"
                        else agent_builder.EDIT_SYSTEM_PROMPT
                    ),
                    user_prompt=_repair_user,
                    model=plan.escalate_model or plan.model,
                    escalate_model=plan.escalate_model,
                    execute=operations.execute,
                    max_steps=max(int(plan.steps), 24),
                    emit=operations.emit,
                    user_id=str(ids.user_id),
                    project_id=str(ids.project_id),
                    require_green_before_done=get_settings().agent_require_green_before_done,
                    ship_green_on_abort=get_settings().agent_ship_green_on_abort,
                    edit_mode=True,
                )
            except Exception as _rep_exc:
                raise_if_terminal_cell_error(_rep_exc)
                print(f"[PP] edit_auto_repair run failed: {_rep_exc!r}", flush=True)
                break
            if _rep.files:
                files.update(_rep.files)
            # Re-probe green after this repair attempt.
            try:
                _rt2 = await operations.probe_runtime("/")
                _runtime_ok = bool(_rt2.get("ok"))
                _rt_error = (
                    ""
                    if _runtime_ok
                    else str(_rt2.get("error") or _rt2.get("status_code") or "5xx")
                )
            except Exception as exc:
                raise_if_terminal_cell_error(exc)
                _runtime_ok = True
            try:
                _tc2 = await operations.probe_build()
                _typecheck_ok = bool(_tc2.get("ok", True))
                _tc_error = (
                    ""
                    if _typecheck_ok
                    else (str(_tc2.get("detail") or "").splitlines() or [""])[0][:240]
                )
            except Exception as exc:
                raise_if_terminal_cell_error(exc)
                _typecheck_ok = True
        if files and _typecheck_ok and _runtime_ok:
            accumulated = "Готово — правка применена."
            print(
                f"[PP] edit_auto_repair SUCCESS after {_ar} attempt(s)",
                flush=True,
            )
        else:
            print(
                f"[PP] edit_auto_repair exhausted attempts={_ar} "
                f"files={len(files)} tc={_typecheck_ok} rt={_runtime_ok}",
                flush=True,
            )

    return CandidateProbeResult(accumulated, _runtime_ok, _typecheck_ok, _rt_error, _tc_error)


async def apply_legacy_design_and_result_text(
    *,
    _agent_res: agent_builder.AgentResult,
    _is_edit: bool,
    _runtime_ok: bool,
    _typecheck_ok: bool,
    accumulated: str,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
) -> tuple[str]:
    # Honest result: a loop-guard abort (looping/exploring) that STILL
    # wrote files, whose app SERVES, AND whose typecheck is CLEAN is not a
    # failure — the work landed, the guard just tripped. Don't scare the
    # user with «Сборка прервана» when the preview is live and clean. A red
    # typecheck (above) excludes this path → no false «Готово».
    if (
        not _agent_res.done
        and _agent_res.stop_reason in ("looping", "exploring")
        and files
        and _runtime_ok
        and _typecheck_ok
    ):
        accumulated = "Готово — правка применена." if _is_edit else "Готово — приложение собрано."
        print(
            "[PP] agentic_build looped-but-serves → reported as done",
            flush=True,
        )
    # Honest result (edit no-op): a surgical follow-up that tripped a loop/
    # stall guard and wrote NOTHING, but left the app GREEN (serves +
    # typecheck clean), broke nothing — the prior build is intact. Saying
    # «Сборка прервана»/«Не удалось завершить правку» here is the false alarm
    # the owner hit live (stop=looping, 0 files, app green). Tell the truth:
    # unchanged and working; invite a re-phrase. Edits only — a FIRST build
    # with 0 files genuinely failed (the scaffold always typechecks green),
    # so it keeps its failure message.
    elif not _agent_res.done and _is_edit and not files and _runtime_ok and _typecheck_ok:
        accumulated = (
            "Не смог применить правку за отведённые шаги — приложение не "
            "изменилось и осталось рабочим. Переформулируй запрос или нажми "
            "«Починить»."
        )
        print(
            "[PP] agentic_build edit no-op on green app → honest no-change",
            flush=True,
        )

    return (accumulated,)


async def check_runtime_security_gates(
    *,
    _is_edit: bool,
    _orch_name: str | None,
    accumulated: str,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> tuple[list[tuple[str, Any]] | None, str, str]:
    _gate_kind = (
        "functional"
        if _orch_name == "nextjs-realtime"
        else "isolation"
        if _orch_name == "nextjs-postgres-drizzle"
        else None
    )
    if _gate_kind == "functional":
        _gate_touched = bool(_is_edit) and any(
            _p.endswith((".tsx", ".ts", ".css"))
            and (
                "chat" in _p
                or "realtime" in _p
                or "layout" in _p
                or "/components/" in _p
                or _p.endswith("globals.css")
            )
            for _p in files
        )
    elif _gate_kind == "isolation":
        # A data app starts leaking when a route handler or its data layer
        # (schema / lib / api) changes — re-prove isolation on those edits.
        _gate_touched = bool(_is_edit) and any(
            _p.endswith(".ts")
            and ("/api/" in _p or "/lib/" in _p or "schema" in _p or _p.endswith("route.ts"))
            for _p in files
        )
    else:
        _gate_touched = False
    # Gate verdicts captured at the gate-loop settle, for the DB attestation
    # persisted with this build's snapshot below (best-effort; None if no gate ran).
    _att_capture: list[tuple[str, Any]] | None = None
    _attestation_stack = _orch_name or project_info.template
    try:
        if (
            get_settings().use_runtime_gates
            and not get_settings().use_native_agent
            and (not _is_edit or _gate_touched)
            and _gate_kind is not None
        ):
            from yleum_api.services import agent_gate_feedback as _agf2

            _rg_attempt = 0
            _rg_max = max(0, int(get_settings().agent_gate_max_attempts))
            while True:
                _base = await operations.preview_url()
                if not _base:
                    print("[PP] runtime_gate: no dev_url — skip", flush=True)
                    break
                if _gate_kind == "functional":
                    from yleum_api.services.functional_gate import run_functional_gate

                    _runtime_verdict: Any = await run_functional_gate(_base)
                else:
                    from yleum_api.services import isolation_gate

                    _runtime_verdict = await isolation_gate.run_public_access_gate(
                        ids.project_id, project_info.slug, _base
                    )
                _fout = [
                    _agf2.outcome_from_checks(
                        _gate_kind,
                        _runtime_verdict.passed,
                        _runtime_verdict.checks,
                    )
                ]
                # Transport-surface security (G005) — stack-agnostic, runs
                # alongside the functional/isolation leg through the SAME
                # blocking heal loop. Was built+unit-tested but UNWIRED
                # ("галочка стоит, гейт мёртвый"); now live. Blocks only on
                # product guarantees (nosniff present, CORS not wildcard-
                # with-credentials) → never false-blocks a good build.
                _att_sec = None
                if get_settings().use_security_gate:
                    from yleum_api.services import security_gate as _secg

                    _security_verdict = await _secg.run_security_gate(_base)
                    _att_sec = _security_verdict
                    _fout.append(
                        _agf2.outcome_from_checks(
                            "security",
                            _security_verdict.passed,
                            _security_verdict.checks,
                        )
                    )
                _fix = _agf2.build_fix_instruction(_fout, _rg_attempt, _rg_max, stack=_orch_name)
                if _fix is None:
                    # Signed, tamper-evident attestation of the FINAL gate
                    # verdicts (best-effort, additive — never affects the
                    # build). Fresh-plan Step 3 "saved attestation".
                    if get_settings().use_build_attestation:
                        try:
                            from yleum_api.services import attestation as _att

                            _att_gates: list[tuple[str, Any]] = [(_gate_kind, _runtime_verdict)]
                            if _att_sec is not None:
                                _att_gates.append(("security", _att_sec))
                            _attn = _att.build_attestation(
                                gates=_att_gates,
                                stack=_attestation_stack,
                                project_id=str(ids.project_id),
                                created_at=_att.now_iso(),
                            )
                            print(
                                f"[ATTEST] {_att.to_log_line(_attn)}",
                                flush=True,
                            )
                            _att_capture = _att_gates
                        except Exception as _att_exc:  # never break a build
                            raise_if_terminal_cell_error(_att_exc)
                            print(f"[ATTEST] skipped: {_att_exc}", flush=True)
                    if not _runtime_verdict.passed:
                        accumulated += (
                            "\n\n⚠️ Проверка работоспособности/безопасности не "
                            "прошла — открой превью и уточни запрос."
                        )
                        print(
                            f"[PP] runtime_gate {_gate_kind} FAIL (out of attempts)",
                            flush=True,
                        )
                    else:
                        print(f"[PP] runtime_gate {_gate_kind} PASS", flush=True)
                    break
                _rg_attempt += 1
                print(
                    f"[PP] runtime_gate {_gate_kind} heal attempt={_rg_attempt}",
                    flush=True,
                )
                _fheal = await agent_builder.run_agent_build(
                    system_prompt=plan.stack_system,
                    user_prompt=_fix + plan.seed_context,
                    model=plan.escalate_model or plan.model,
                    execute=operations.execute,
                    max_steps=plan.steps,
                    emit=operations.emit,
                    user_id=str(ids.user_id),
                    project_id=str(ids.project_id),
                )
                # Never regress a clean build: accept the heal's files ONLY
                # if it left the typecheck GREEN. A failed/unverifiable heal
                # must NOT overwrite the clean done-build snapshot — the worst
                # outcome of an enforcement gate is shipping WORSE code than
                # gate-off. Unknown ≠ green here (opposite of the done-build
                # check): only an explicit ok=True earns the merge.
                _heal_green = False
                try:
                    _hc = await operations.probe_build()
                    _heal_green = bool(_hc.get("ok", False))
                except Exception as _hc_exc:
                    raise_if_terminal_cell_error(_hc_exc)
                    print(
                        f"[PP] runtime_gate heal verify skipped: {_hc_exc!r}",
                        flush=True,
                    )
                if _heal_green:
                    files.update(_fheal.files)
                else:
                    print(
                        "[PP] runtime_gate heal not green — discarding heal, keeping clean build",
                        flush=True,
                    )
                    break
    except Exception as _rg_exc:  # a gate must never crash the build
        raise_if_terminal_cell_error(_rg_exc)
        print(f"[PP] runtime_gate skipped: {_rg_exc!r}", flush=True)

    return _att_capture, _attestation_stack, accumulated


async def check_coverage_gate(
    *,
    _att_capture: list[tuple[str, Any]] | None,
    _build_plan: Any,
    _is_edit: bool,
    _orch_name: str | None,
    accumulated: str,
    factory: async_sessionmaker[AsyncSession],
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> tuple[list[tuple[str, Any]] | None, str]:
    _coverage_verdict = None
    try:
        # A2: also run on EDITS that touch a capability surface (api route
        # handler / lib / page), so an edit that 4xx-breaks a working action
        # is caught — not only fresh builds. (_build_plan is read back from
        # the persisted spec for edits by the injection block above.)
        _cov_edit_touched = bool(_is_edit) and any(
            (_p.endswith("route.ts") or "/api/" in _p or "/lib/" in _p or _p.endswith("page.tsx"))
            for _p in files
        )
        _cov_on = (
            get_settings().use_coverage_gate
            and not get_settings().use_native_agent
            and _build_plan is not None
            and not _build_plan.is_empty
            and (not _is_edit or _cov_edit_touched)
        )
        if _cov_on and _build_plan is not None:
            from yleum_api.services import agent_gate_feedback as _agf3
            from yleum_api.services import app_errors as _app_errors
            from yleum_api.services import coverage_gate as _covg

            _cov_attempt = 0
            _cov_max = max(0, int(get_settings().coverage_max_attempts))
            while True:
                _known = _covg.api_routes_from_files(files)
                _cell_preview = (
                    await runtime.handle.create_preview_session()
                    if runtime.handle is not None
                    else None
                )
                _cv = await _covg.run_coverage_gate(
                    ids.project_id,
                    _build_plan,
                    stack=_orch_name or project_info.template,
                    known_routes=_known or None,
                    cell_preview=_cell_preview,
                )
                _coverage_verdict = _cv
                # A1: heal HARD gaps (route exists, wrong status) ALWAYS; a
                # missing route (planner over-specified) is healed once then
                # dropped to advisory — never a hard block / heal-storm on a
                # working app.
                _active_checks = [
                    c
                    for c in _cv.checks
                    if not c.ok and (c.kind == "wrong_status" or _cov_attempt == 0)
                ]
                _cout = [
                    _agf3.GateOutcome(
                        name="coverage",
                        passed=not _active_checks,
                        failures=[f"{c.name}: {c.detail}" for c in _active_checks],
                    )
                ]
                _cfix = _agf3.build_fix_instruction(_cout, _cov_attempt, _cov_max, stack=_orch_name)
                if _cfix is None:
                    _hard_left = _cv.hard_missing()
                    _soft_left = _cv.soft_missing()
                    if _hard_left:
                        _miss = ", ".join(_hard_left[:8])
                        accumulated += (
                            f"\n\n⚠️ Готово {_cv.covered} из {_cv.total} "
                            f"ключевых функций — пока не работают: {_miss}."
                        )
                        try:
                            await _app_errors.publish(
                                factory,
                                ids.project_id,
                                ids.assistant_message_id,
                                category="incomplete",
                                title=(f"Готово {_cv.covered} из {_cv.total} функций"),
                                detail=("Эти возможности пока не отвечают как ожидалось: " + _miss),
                                fixable=True,
                            )
                        except Exception as _ce_exc:
                            print(
                                f"[PP] coverage incomplete card skipped: {_ce_exc!r}",
                                flush=True,
                            )
                        print(
                            f"[PP] coverage_gate FAIL hard={_hard_left} "
                            f"({_cv.covered}/{_cv.total})",
                            flush=True,
                        )
                    elif _soft_left:
                        print(
                            "[PP] coverage_gate soft-only (advisory) "
                            f"{_soft_left} — not blocking a good app",
                            flush=True,
                        )
                    else:
                        print(
                            f"[PP] coverage_gate PASS {_cv.covered}/{_cv.total}",
                            flush=True,
                        )
                    break
                _cov_attempt += 1
                print(
                    f"[PP] coverage_gate heal attempt={_cov_attempt} ({_cv.covered}/{_cv.total})",
                    flush=True,
                )
                _cheal = await agent_builder.run_agent_build(
                    system_prompt=plan.stack_system,
                    user_prompt=_cfix + plan.seed_context,
                    model=plan.escalate_model or plan.model,
                    execute=operations.execute,
                    max_steps=plan.steps,
                    emit=operations.emit,
                    user_id=str(ids.user_id),
                    project_id=str(ids.project_id),
                )
                # Never regress a clean build: accept the heal's files ONLY
                # if the typecheck stayed GREEN (same rule as the runtime
                # gate). A non-green / unverifiable heal is discarded.
                _cov_green = False
                try:
                    _cc = await operations.probe_build()
                    _cov_green = bool(_cc.get("ok", False))
                except Exception as _cc_exc:
                    raise_if_terminal_cell_error(_cc_exc)
                    print(
                        f"[PP] coverage heal verify skipped: {_cc_exc!r}",
                        flush=True,
                    )
                if _cov_green:
                    files.update(_cheal.files)
                else:
                    print(
                        "[PP] coverage heal not green — discarding, keeping clean build",
                        flush=True,
                    )
                    break
    except Exception as _cov_exc:  # a gate must never crash the build
        raise_if_terminal_cell_error(_cov_exc)
        print(f"[PP] coverage_gate skipped: {_cov_exc!r}", flush=True)

    if (
        _coverage_verdict is not None
        and _coverage_verdict.hard_missing()
        and get_settings().use_build_attestation
    ):
        _att_capture = _capture_hard_coverage_attestation(
            _att_capture,
            _coverage_verdict,
            enabled=True,
        )

    return _att_capture, accumulated
