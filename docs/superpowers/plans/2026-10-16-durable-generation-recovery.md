# Durable Generation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Генерация сохраняет задачу, выходит из повторов, безопасно продолжается после восстановимого сбоя и завершает её только с проверенным результатом.

**Architecture:** Развиваем существующие GenerationRun, coordinator, worker ownership и proof-bound publication. Новые решения находятся в небольших модулях recovery; legacy-поведение и старые записи остаются совместимыми. Сначала поставляем readers и защиту выкладки, затем включаем v1 для изолированных canary.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy/asyncpg, PostgreSQL 16, Redis, закрытое S3/MinIO; Next.js 15.5.24, React, TypeScript, Vitest/Playwright; существующие production compose и release scripts. Пакетный менеджер web — pnpm 9.15.0.

**Spec:** `docs/superpowers/specs/2026-10-16-durable-generation-recovery-design.md`, утверждён пользователем 16 октября 2026. План подготовлен по `de25facd` / runtime-базе `2c2b9b81`; перечисленные ниже новые файлы и интерфейсы ещё не реализованы.

## Global Constraints

- «Требования одного запуска неизменны и привязаны к его исходному `prompt_hash`». Новый запрос — новый связанный run и новый idempotency key, после безопасного завершения прежнего.
- «`finished_at` не выставляется при ожидании». Подрежимы v1: `working`, `repairing`, `waiting_dependency`, `reconciling`, `verifying`, `needs_input`; статус БД остаётся `running`, уникальный active-run индекс сохраняется.
- «`execution_started_at` не очищается, первоначальный dispatch заново не выполняется». Для продолжения нужен отдельный атомарно захваченный attempt.
- «Полностью неполный пакет команд не выполняется». Счётчик обрезок — с последнего подтверждённого прогресса, отдельная общая история сохраняется.
- «Не вводим новый потолок `max_tokens` для обычного Sonnet-запроса: сохраняем проверенное поведение omission».
- «После завершения срок хранения по умолчанию семь суток с настраиваемым TTL». Ответы активных задач и незаконченных сверок не удаляются.
- «Отсутствие доказательства закрывает выкладку». Ожидание само по себе не освобождает от проверки writers, leases, fences и drain.
- Не ослаблять schema/ownership/security/build/runtime/release gates. Ни checkpoint, ни старая зелёная сборка не создают готовый snapshot или успешное списание за завершённый продукт.
- Не переносить текущую привязку amoCRM-бота, не удалять реальные данные, не переигрывать SQL/интеграционные действия. Миграции — только добавляющие, с проверенной копией и проверкой прежних строк.
- Каждая задача включает локальный рефакторинг затронутой функции и непосредственных collaborators; unrelated cleanup не включать. Все Git и deployment действия выполняет один координатор.
- Каждая поставка: RED → GREEN → независимое ревью → commit/push → exact-revision CI → штатный последовательный deploy → health/live evidence. При недоставленной поставке не начинать следующий product change; исследования и подготовка плана допустимы.
- План не разрешает реализацию сам по себе: сначала пользователь просматривает его и подтверждает способ исполнения. Новые платные canary — только в стадии живой приёмки при доступном балансе и конкретном пробеле доказательств.

## Review Focus

1. Часы сдвинулись назад или worker повторно поднял задачу: ожидание не обнуляет потраченные деньги и работу; Task 11 добавляет `test_clock_jump_and_resume_do_not_restore_spend_allowance`.
2. Во время ожидания поменялись исходники, схема или владелец: старый план не перезаписывает новые данные; Tasks 2/9 добавляют `test_resume_rejects_changed_binding_without_writes`.
3. Появился неизвестный формат recovery или старый worker: без понятного контракта нет продолжения и writer cutover; Tasks 2/10 добавляют `test_unknown_contract_is_preserved_and_blocks_activation`.
4. Объект ответа записался частично, повреждён или попал под очистку: его нельзя исполнять как целый, активная сверка сохраняется; Task 8 добавляет `test_partial_object_and_cleanup_never_certify_response`.
5. Провайдер ответил, но процесс умер на границе сохранения: деньги и текст — разные доказательства, второго upstream POST нет; Tasks 7–9 добавляют `test_crash_after_dispatch_has_no_second_provider_post`.

## File structure and execution order

Все пути ниже — от корня репозитория. Новые модули не дублируют существующие finalization permits, schema checks или storage formats.

| Responsibility | New module | Existing integration points |
| --- | --- | --- |
| Причинная трасса | `apps/api/src/yleum_api/services/generation/recovery_trace.py` | `generation/progress.py`, `generation_events.py`, gateway provider evidence |
| Версионированные записи и checkpoint | `generation/recovery_contract.py`, `generation/recovery_store.py` в том же API services каталоге | `generation_runs.py`, `generation/source_checkpoint.py`, `models/generation_run.py` |
| Требования и привязанные проверки | `generation/recovery_requirements.py` | `build_spec.py`, `max_behavior_proof.py`, `agent_pipeline.py` |
| Прогресс, стратегия, контекст | `generation/recovery_policy.py`, `generation/recovery_context.py` | `agent_builder.py`, `agent_native.py`, `generation/continuation.py` |
| Безопасный целевой ремонт | `generation/source_error_classification.py` | `max_finalization.py`, `project_cell_executor.py` |
| Идентичность вызова модели | gateway `services/generation_attempts.py`; API `models/generation_provider_attempt.py` | API Alembic, gateway `services/provider_calls.py`, chat/native routes |
| Закрытое хранение ответа | gateway `services/generation_response_store.py`, `core/generation_response_auth.py`, `routers/generation_responses.py` | gateway config/transport, API `llm_client.py` |
| Yield, claim и продолжение | API `generation/recovery_execution.py`, `generation/recovery_reconciliation.py` | `generation/lifecycle.py`, `generation/supervisor.py`, `workers/generation.py` |
| Quiescence и readers | API `generation/recovery_quiescence.py` | `generation_deployment_drain.py`, release scripts, worker heartbeat |
| Честный статус и проверка функций | web `lib/generation-recovery.ts`; API `generation/recovery_acceptance.py` | текущие progress/status/preview и finalization |

Зависимости: 1 → 2 → 3 → 4 → 5 → 6; 2 → 7 → 8; 2+4+7+8 → 9 → 10 → 11 → 12; 3+9+11 → 13; 1 → 14; все → 15. Выполнять последовательно по номерам: порядок намеренно оставляет активацию последней. API files с префиксом `generation/` находятся в `apps/api/src/yleum_api/services/`.

## Verification and delivery conventions

API tests запускаются из `apps/api`: `uv run --frozen pytest -o addopts='' -q <files>`; lint/types: `uv run ruff check .` и `uv run mypy src`. Интеграционные тесты используют только disposable PostgreSQL 16 и Redis 7 с отдельным `DATABASE_TEST_URL`, как `.github/workflows/ci.yml`; production DSN никогда не подставляется. Gateway tests — из `apps/llm-gateway`: `uv run --frozen --extra dev pytest -q -p no:warnings <files>`. Совместные PostgreSQL tests используют точную настройку двух source roots и gateway packages из CI, а не новый неиспытанный runner. Web — из `apps/web`: `pnpm typecheck`, `pnpm test <files>`, `pnpm lint`, `pnpm build`.

В каждом RED ниже тест должен упасть по указанному поведению. Ошибка окружения, отсутствие тестовой БД или установка пакета не считаются RED продукта. Для documentation-only изменения достаточно diff/reference sanity и CI; прошедшие проверки неизменённого runtime повторно вручную не гонять.

Общий шаг доставки каждой product task: review → scoped commit с указанным сообщением → push/PR → все обязательные проверки exact head → merge по политике репозитория → `infra/release/deploy-prod.sh <full-main-sha>` с `--no-web` при неизменном web и `--gateway` при изменённом gateway → полный штатный health/smoke с ревизиями всех затронутых сервисов. Не запускать скрипт при активных writers и не обходить drain. Документировать точный blocker, если этот шаг недоступен.

При выполнении каждой задачи общий шаг разворачивается в отдельные отметки: `[ ] review passed`, `[ ] scoped commit`, `[ ] pushed remote SHA matches`, `[ ] exact CI green`, `[ ] merged main SHA verified`, `[ ] canonical deploy`, `[ ] health/live receipt`. Это повторяемый checklist каждой поставки, а не разрешение отмечать её целиком по одному зелёному тесту.

### Task 1: Причинная трасса без раскрытия содержимого запросов

**Files:** Create `apps/api/src/yleum_api/services/generation/recovery_trace.py`; modify `services/generation/progress.py`, `services/generation_events.py`, `services/agent_progress.py`, `apps/llm-gateway/src/yleum_gateway/providers/native_stream.py`; test `apps/api/tests/test_generation_recovery_trace.py`, existing `test_generation_output_diagnostics.py`, gateway `tests/test_native_stream_receipt.py`.

**Interfaces:** `safe_recovery_trace(payload: Mapping[str, object]) -> dict[str, object]` accepts only run/attempt/operation IDs, source/proof hashes, stage, finish reason, sent-or-omitted max, token counters, transport stage, retry decision and normalized error code. `GenerationProgress.record_generation_event` remains the durable event writer; full raw responses do not enter that stream.

- [ ] Write `test_trace_links_call_and_source_without_secrets` and `test_console_error_keeps_source_stack_and_url_safely`: assert retained IDs/stage/error origin, omitted auth/cookies/tokens and message bodies, redacted URL query secrets; existing 4KiB-string/16KiB-event bounds hold.

  ```python
  safe = safe_recovery_trace({"run_id": "run-1", "stage": "repair", "authorization": "private"})
  assert safe["run_id"] == "run-1" and safe["stage"] == "repair"
  assert "authorization" not in safe and "private" not in str(safe)
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_trace.py`; RED: missing correlation or leaked field, not a network call.
- [ ] Implement the allowlisted normalizer and attach metadata at existing provider/progress boundaries. Preserve commit-before-publish and classify unknown429 as unknown. Extract only the touched duplicated diagnostic formatting.
- [ ] Run the new API tests and existing diagnostics/progress-security/event tests; run gateway receipt tests, relevant Ruff/mypy. Simulate complete, truncated and disconnected SSE with no real provider.
- [ ] Independently review and complete the common delivery step; commit `feat(generation): preserve causal recovery traces`. Activation remains off.

### Task 2: Immutable recovery contract, checkpoints and shadow mode

**Files:** Create API `generation/recovery_contract.py`, `generation/recovery_store.py`; modify `generation/source_checkpoint.py`, `generation/contracts.py`, `core/config.py`; test `apps/api/tests/test_generation_recovery_contract.py`, `test_generation_recovery_store.py`, existing `test_source_checkpoint.py`, `test_generation_runs.py`.

**Interfaces:** Use strict Pydantic records: `RecoveryIdentity(run_id, project_id, user_id: UUID; prompt_hash: str; accepted_snapshot_id: UUID|None; supersedes_run_id: UUID|None)`; `RecoveryBinding(source_sha256, schema_sha256, environment_sha256, workspace_id: str; fencing_epoch: int)`; `CheckpointRef(object_key, sha256: str)`; `IncompleteResponseBudget(total: int, since_progress: int)`. `RecoveryState` has version1, identity, `binding: RecoveryBinding|None`, `checkpoint/context_ref: CheckpointRef|None`, `cursor: Literal['bootstrap','agent','finalization']`, `requirements: tuple[RecoveryRequirement,...]`, `witnesses: tuple[RequirementWitness,...]`, nonnegative `attempt_seq/state_revision/consecutive_no_progress/active_work_ms`, `stage_active_work_ms: dict[Literal['edit','repair','proof'],int]`, `phase: Literal['working','repairing','waiting_dependency','reconciling','verifying','needs_input']`, `mode: Literal['normal','diagnose','narrow','fresh_context','blocked']`, `next_check_at: datetime|None`, unresolved operation UUIDs, tried-strategy strings, incomplete-response budget, and `continuation_claim: ContinuationClaim|None`. Binding/checkpoint may be absent only before product work at bootstrap; model/tool editing requires both and a persisted immutable context reference. Define the requirement/witness, observation/directive and continuation-claim records with the exact fields listed in Tasks3/4/9 now, so the reader schema is complete before behavior is added. `decode_recovery(raw: object) -> RecoveryState|None` returns None only for absent v1; unsupported/corrupt versions raise `RecoveryContractError` without overwriting raw state. `save_recovery_checkpoint(session: AsyncSession, *, run_id: UUID, expected_attempt: int, expected_revision: int, state: RecoveryState, files: Mapping[str,str], deleted_paths: Sequence[str]) -> CheckpointRef` validates ownership/baseline/fence and CAS revision before committing an immutable reference.

- [ ] Write tests for roundtrip, unknown version (`test_unknown_contract_is_preserved_and_blocks_activation`), foreign owner, stale attempt/fence, changed source/schema and storage failure. Assertions: invalid input makes no source write; prior `agent_state` fields survive; no accepted snapshot/publication is created.

  ```python
  assert decode_recovery(None) is None
  with pytest.raises(RecoveryContractError):
      decode_recovery({"version": 99})
  assert run.agent_state["unrelated"] == original_unrelated_state
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_contract.py tests/test_generation_recovery_store.py`; RED: absent validation/preservation.
- [ ] Implement a namespaced `agent_state['recovery_v1']`, reusing the existing private checkpoint format and content digest. Do not broaden `adjacent_checkpoint`: explicit adjacent failed-run recovery remains separate from same-run resume. Add `GENERATION_RECOVERY_MODE=off|shadow|canary|on`, default `off`, and `GENERATION_RECOVERY_CANARY_PROJECT_IDS`, default empty. Shadow stores observations in the separate `recovery_shadow_v1` namespace, without changing dispatch/finalization or making paid calls; it never creates an active recovery contract.
- [ ] Run new tests plus source-checkpoint/run tests on disposable services; assert unknown state survives serialization, CAS conflicts are visible, and unavailable checkpoint storage does not release or destroy the only retained source.
- [ ] Review and deliver; commit `feat(generation): add versioned recovery checkpoints`. Readers are shipped, v1 execution still off.

### Task 3: Frozen requirements with source-bound acceptance witnesses

**Files:** Create API `generation/recovery_requirements.py`; modify `services/build_spec.py`, `services/max_behavior_proof.py`, `generation/agent_pipeline.py`, `generation/recovery_contract.py`, `schemas/message.py`, `generation/acceptance.py`; test `test_generation_recovery_requirements.py`, existing `test_named_behavior_preflight.py`, `test_max_behavior_proof.py`, `test_generation_runs.py`.

**Interfaces:** Task2 records: `RecoveryRequirement(id, text, origin_prompt_sha256, check_kind: str; required: bool)`; allowed check kinds `named_behavior|http|browser|db_persistence|max_identity|manual`. `RequirementWitness(requirement_id, receipt_id, source_sha256, schema_sha256, environment_sha256: str; passed: bool)` is constructed only after validating an existing server receipt, never from model output. Add `load_requirement_witness(session: AsyncSession, *, run_id: UUID, requirement_id: str, receipt_id: str, binding: RecoveryBinding) -> RequirementWitness`; it rejects mismatched proof owner/run/request and candidate. `freeze_requirements(prompt: str, spec: FidelitySpec, named: NamedBehaviorContract|None) -> tuple[RecoveryRequirement,...]`; `requirements_complete(requirements: Sequence[RecoveryRequirement], witnesses: Sequence[RequirementWitness], binding: RecoveryBinding) -> bool` requires current PASS for each required ID, and treats unverified/manual requirements as incomplete.

- [ ] Write tests `test_unknown_requirement_is_pending_not_dropped`, `test_model_cannot_mark_requirement_passed`, `test_old_source_witness_does_not_certify_new_candidate`, `test_amended_prompt_has_new_run_identity`. Assertions: IDs/manifest digest stable; unsupported requirement stays pending; original `prompt_hash` never changes; old answer/PASS cannot certify the new run.

  ```python
  assert not requirements_complete(requirements, [], current_binding)
  assert not requirements_complete(requirements, old_source_witnesses, current_binding)
  assert amended_run.id != original_run.id and original_run.prompt_hash == original_hash
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_requirements.py`; RED on dropped requirement or stale PASS.
- [ ] Freeze the original request reference plus existing build/named contracts; keep model-proposed additions distinct from trusted witnesses. Do not replace whole-request understanding with a few keyword matches. Add optional `PromptRequest.supersedes_run_id: UUID|None=None`; validate same authenticated user/project and terminal, physically quiesced predecessor before normal admission. Requests without this field keep old behavior. User amendments use existing cancellation/admission and a new linked run; never reassign the old identity. Keep general unsupported requirements explicit rather than silently approved.
- [ ] Run new tests with existing named behavior, idempotency and ownership tests; verify requirements storage alone does not introduce an extra planner call or publish anything.
- [ ] Review and deliver; commit `feat(generation): freeze requirements and acceptance witnesses`. Hard completion gate remains off until Task 13.

### Task 4: Semantic progress, strategy changes and a focused context handoff

**Files:** Create API `generation/recovery_policy.py`, `generation/recovery_context.py`; modify `generation/continuation.py`, `generation/contracts.py`, `generation/agent_generation.py`, `services/agent_builder.py`, `services/agent_native.py`; test `test_generation_recovery_policy.py`, `test_generation_recovery_context.py`, existing `test_generation_continuation.py`, `test_agent_source_progress.py`.

**Interfaces:** `RecoveryObservation(kind: Literal['source','diagnostics','proof','tool','truncated','dependency']; binding: RecoveryBinding; requirement_id: str|None; action_signature: str|None; diagnostic_signatures: tuple[str,...]; witness: RequirementWitness|None)`; `RecoveryDirective(action: Literal['continue','nudge','narrow','reset_context','yield','needs_input']; reason: str)`. `observe_recovery(state: RecoveryState, observation: RecoveryObservation) -> tuple[RecoveryState,RecoveryDirective]` is pure. `build_recovery_context(state: RecoveryState, *, relevant_files: Mapping[str,str], diagnostic: str) -> str` retains original scope, obligations, unresolved intents and failed approaches. Optional `AgentOperations.recovery: Callable[[RecoveryObservation], Awaitable[RecoveryDirective]]|None` is the shared loop seam; old callers default to None.

- [ ] Write fixtures for unchanged source, comment-only churn, ABA, new verified requirement PASS, reduced compiler failures and unchanged green build. Assert repeated signature transitions `nudge → narrow → reset_context → needs_input`; a new verified witness resets the streak; comment-only changes do not. Set one strategy transition after two consecutive equivalent non-progress observations; the full history is retained.

  ```python
  assert [item.action for item in boundary_directives] == ["nudge", "narrow", "reset_context", "needs_input"]
  assert comment_only_state.consecutive_no_progress > 0
  assert verified_progress_state.consecutive_no_progress == 0
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_policy.py tests/test_generation_recovery_context.py`; RED on syntactic novelty counting as semantic success.
- [ ] Implement the deterministic policy around existing source novelty. A source implementation milestone counts only when tied to an open requirement and validated by an independent compiler/test receipt; model claims and tooling counters do not qualify. Unknown-progress cases can get diagnosis time but cannot reset paid-work accounting. Context reset changes memory, not source or schema.
- [ ] Run policy/context and both-loop continuation tests; assert unresolved intents and original prompt survive handoff, while secrets and redundant raw logs are absent. No new model role is introduced.
- [ ] Review and deliver; commit `feat(generation): guide recovery by verified progress`. New directives run in shadow until lifecycle support is ready.

### Task 5: Recover separated truncations and avoid redundant pure checks

**Files:** Modify `services/agent_native.py`, `services/agent_builder.py`, API `generation/recovery_policy.py`; test existing `test_agent_native_truncation.py`, `test_agent_native_output_limit.py`, `test_agent_builder.py`, plus `test_generation_recovery_actions.py`.

**Interfaces:** Task2's `IncompleteResponseBudget(total: int, since_progress: int)` and `record_output_boundary(budget: IncompleteResponseBudget, *, truncated: bool, verified_progress: bool) -> IncompleteResponseBudget` in recovery policy. Truncation increments both counters; a separate validated progress observation resets only since_progress; neither event changes total paid work. Reject a claimed simultaneous truncated batch/progress event. Keep the existing threshold of three responses without verified progress, but do not terminalize separated, genuinely recovered truncations. Pure check dedup key is `(tool,args_digest,source_hash,schema_hash,dependency_hash,environment_hash,fence)`; no business write, migration or unknown command can be deduplicated by name. Treat only executor-declared read-only fast checks as pure; a generic full build is not assumed pure.

- [ ] Add `test_three_truncations_with_verified_progress_continue`: each bad batch contains a forbidden action; two real edits and current successful receipts separate the failures. Assert no forbidden action, final requested change accepted, total=3 and consecutive counter reset. Add `test_duplicate_pure_build_same_batch_executes_once` for an executor-declared fast check, `test_full_build_is_not_assumed_pure`, and `test_schema_or_dependency_change_invalidates_check`.

  ```python
  assert "FORBIDDEN_PARTIAL_ACTION" not in executed_actions
  assert budget.total == 3 and accepted_current_candidate
  assert unchanged_pure_check_calls == 1 and changed_schema_check_calls == 2
  ```
- [ ] Run focused new tests in the files above; RED: the third separated truncation stops or duplicate unchanged check executes twice.
- [ ] Apply the common progress witness to both loops; retain all-batch rejection before execution, cancellation, transport ambiguity and stale-proof rejection. A failed or unchanged build does not reset the counter. Dedup only explicitly pure executor checks within one provider batch and the same validated binding; clear reuse after any mutating/unknown action, and do not add a global cache.
- [ ] Run all truncation/output-limit/legacy stop-reason and action-cycle regressions, plus actual-wire omission tests in gateway. Assert neither incomplete tools nor prior side effects replay, and no new `max_tokens` is sent for ordinary Sonnet.
- [ ] Review and deliver; commit `fix(generation): recover separated truncations without replay`. Native stays disabled unless independently selected by existing policy.

### Task 6: Classify repairable source errors without editing infrastructure

**Files:** Create API `generation/source_error_classification.py`; modify `services/max_finalization.py`, `generation/agent_finalization.py`; test existing `test_max_finalization_missing_import.py`, `test_max_finalization_progress.py`, new `test_generation_source_error_classification.py`.

**Interfaces:** `SourceFailure(kind: Literal['relative_import','workspace_alias','type_error','infrastructure','external_package','unknown']; importer: str|None; allowed_paths: tuple[str,...])`; `classify_source_failure(detail: str, *, files: Mapping[str,str], command_finished: bool, frozen_source_sha256: str) -> SourceFailure`. Resolve aliases only from the frozen tsconfig within the workspace; return explicit infrastructure/external/unknown dispositions rather than broad repair authorization.

- [ ] Add cases missing `./flower.css`, known `@/components/Missing`, traversal/symlink escape, multiple alias targets, absent external package, network failure and TS2554. Assert only trusted workspace source gets allowed repair paths; package/network/unfinished logs never do. TS2554 must retain its concrete diagnostic instead of being replaced with a generic output-limit label.

  ```python
  assert trusted_alias.kind == "workspace_alias" and trusted_alias.allowed_paths
  assert not traversal.allowed_paths and not external_package.allowed_paths
  assert "TS2554" in retained_diagnostic
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_source_error_classification.py tests/test_max_finalization_missing_import.py`; RED on rejected verified alias or unauthorized external repair.
- [ ] Extract the local classification out of the large finalization module; preserve frozen-source/finished-command identity checks and the existing repair callback. Repairs must change intended source and be followed by fresh compiler/build/runtime evidence; no-op repair does not buy another full build.
- [ ] Run real offline Next fixtures for missing CSS and alias before/after repair plus full negative classifier tests. Preserve schema/migration and ownership gates.
- [ ] Review and deliver; commit `fix(generation): distinguish source repair from environment failures`.

### Task 7: Durable identity for each generation provider attempt

**Files:** Create API `models/generation_provider_attempt.py`, migration `migrations/versions/0086_generation_provider_attempts.py`; modify `models/__init__.py`, API `generation/recovery_contract.py` and gateway `services/provider_calls.py`; create gateway `services/generation_attempts.py`; test gateway `tests/test_generation_attempts.py`, `integration_tests/test_generation_attempts_postgres.py`, API `test_migrations_single_head.py`.

**Interfaces:** `GenerationProviderIdentity(run_id, project_id, user_id, attempt_id: UUID; prompt_hash, request_sha256: str)`; API persists attempt intent before sending to gateway. Table `generation_provider_attempts` has `attempt_id` PK, immutable identity/digest, unique nullable `provider_call_id`, `dispatch_token: UUID`, phase `reserved|dispatching|response_stored|indeterminate|closed`, response manifest/hash, timestamps/retain_until. No cascading app/user deletion removes financial journal evidence. `AttemptAdmission(action: Literal['dispatch','stored','pending','conflict'], dispatch_token: UUID|None, provider_call_id: UUID|None, response_manifest_key: str|None)`; `reserve_generation_attempt(identity: GenerationProviderIdentity) -> AttemptAdmission`; `mark_attempt_dispatching(attempt_id: UUID, *, dispatch_token: UUID, provider_call_id: UUID) -> None` commits by CAS before upstream POST. Only a reserved, not-yet-dispatched row can rotate its token atomically; a stale holder then cannot dispatch. A dispatching/unknown attempt never gets a second dispatch permit. Existing provider-call accounting remains its single owner.

- [ ] Add real PostgreSQL concurrency/crash tests: two equal keys cause one upstream dispatch; changed body/model under the same key conflicts; `dispatching` after a crash is indeterminate, not permission for replay; unknown cost remains NULL and duplicate upstream IDs stay quarantined.

  ```python
  assert provider_post_count == 1
  assert changed_digest_admission.action == "conflict"
  assert orphaned_dispatch.action == "pending" and journal.calculated_cost_rub is None
  ```
- [ ] Run the new gateway unit/PostgreSQL tests with the existing joint CI setup; RED: two physical POSTs or overwritten identity. Confirm the actual Alembic head first: base is0085; if it moved, rebase/renumber rather than introducing another head.
- [ ] Implement the additive table/ORM and transactional admission. API and gateway validate the same versioned identity JSON locally; neither imports the other application's runtime. Test canonical serialization/digest compatibility. Request digest covers semantic payload, model, tools and sent/omitted budget, excluding authorization. Existing non-generation routes retain their contract. A repeated gateway submission can retrieve/observe the old attempt; it never silently creates a new upstream request. Native runner claims/JTI checks still execute independently; use Task8's separate read path for recovery instead of replaying a consumed runner token.
- [ ] On a disposable restored DB copy, upgrade and verify all previous rows, indexes and provider receipts unchanged; run both shared-billing contract suites and migration single-head tests. Prepare readers before schema-dependent activation.
- [ ] Review and deliver with the verified backup/migration prerequisite; commit `feat(gateway): identify generation attempts before dispatch`, deploy with `--gateway`, recording API/gateway schema compatibility.

### Task 8: Private complete-response retention and authenticated recovery reads

**Files:** Create gateway `services/generation_response_store.py`, `core/generation_response_auth.py`, `routers/generation_responses.py`; modify gateway `core/config.py`, `main.py`, `routers/chat.py`, `routers/messages_native.py`, `providers/native_stream.py`, `pyproject.toml`, `uv.lock`; update API `core/config.py`, `services/llm_client.py` and `apps/llm-gateway/deploy/full/.env.example`, `docker-compose.yml`, `docker-compose.hostdb.yml`; create gateway `tests/test_generation_response_store.py`, `test_generation_response_auth.py`, `test_generation_response_routes.py`, `integration_tests/test_generation_response_retention_postgres.py`.

**Interfaces:** `StoredGenerationResponse(attempt_id: UUID, sha256: str, object_key: str, complete: bool)`; `preflight_response_store(identity: GenerationProviderIdentity) -> None`, `store_generation_response(identity: GenerationProviderIdentity, *, chunks: AsyncIterator[bytes], finish_reason: str) -> StoredGenerationResponse`, `load_generation_response(identity: GenerationProviderIdentity) -> bytes`, `delete_expired_generation_responses(now: datetime) -> int`. API `mint_generation_response_token(identity: GenerationProviderIdentity, *, purpose: Literal['generation_response.record','generation_response.read'], now: datetime) -> str` binds audience `llm-gateway`, exact run/project/user/prompt/attempt/request digest and expiry60 seconds with dedicated `GENERATION_RESPONSE_SIGNING_SECRET`. Internal `GET /_internal/generation-responses/{attempt_id}` requires the read purpose; new generation retention admission requires record purpose. Reuse the proven HMAC primitive pattern, not project-cell one-use JTI. Browser authorization/metadata alone cannot access it.

- [ ] Add auth and storage cases: foreign run/user/prompt/attempt, expired or wrong-purpose token, public bucket policy, incomplete upload, corrupted hash, available cost but absent body, active reconciliation during cleanup. Assertions: deny before loading body; no complete receipt for partial bytes; active objects remain; completed expired objects are cleaned without deleting financial rows.

  ```python
  assert foreign_read.status_code == 403 and object_load_calls == 0
  assert not partial_response.complete
  assert active_object_exists and expired_closed_object_deleted and financial_row_exists
  ```
- [ ] Run new storage/auth/route tests and receipt tests; RED on unsafe access or an incomplete response accepted as recoverable.
- [ ] Use a dedicated private bucket with gateway credentials scoped only to that bucket, using `minio>=7.2,<8.0` and a frozen lock (API currently locks7.2.20). Do not import API runtime into gateway or use public-media bucket helpers. Configure `GENERATION_RESPONSE_RETENTION_SECONDS=604800`; secrets are provisioned through the existing private configuration process, never committed. Store chunks/manifest with final digest only after complete SSE receipt; API unavailable must not prevent gateway from persisting an already obtained response. Preflight storage availability/quota before new provider dispatch.
- [ ] Wire only authenticated generation calls into retention; reject forged server binding against the run record. Generic runtime-AI/message content is not archived automatically. In API add `recover_generation_response(identity) -> dict[str,object]|None`; return None only for a confirmed not-yet-stored response, and preserve a distinct indeterminate outcome. Never replay native runner JTI or mistake a financial receipt for response content.
- [ ] Run gateway unit/joint DB tests, storage privacy/TTL checks, API caller tests, Ruff/types and lock reproducibility. Include slow/large response and crash-after-dispatch fixtures without another paid POST. Review/deliver; commit `feat(gateway): retain private generation responses for recovery`, with `--gateway`.

### Task 9: Explicit durable yield and exactly one continuation owner

**Files:** Create API `generation/recovery_execution.py`, `generation/recovery_reconciliation.py`; modify `generation/lifecycle.py`, `generation/supervisor.py`, `workers/generation.py`, `generation_runs.py`, `generation/agent_pipeline.py`, `services/project_cell_executor.py`; test `test_generation_recovery_execution.py`, `test_generation_recovery_reconciliation.py`, existing worker/cancellation/artifact tests.

**Interfaces:** `GenerationYielded(RuntimeError)` carries `run_id: UUID`, `checkpoint: CheckpointRef|None`, `attempt_seq: int`, `phase: str`; only raise after durable state commit. A missing source reference is legal only at bootstrap before any product effect; otherwise yield requires the saved source. `claim_continuation(session: AsyncSession, *, run_id: UUID, expected_attempt: int, worker_id: str, now: datetime) -> ContinuationClaim|None`; `ContinuationClaim(run_id, token: UUID, attempt_seq: int, worker_id: str, expires_at: datetime)`; `resume_generation(claim: ContinuationClaim) -> None`. `ReconciliationResult(outcome: Literal['settled','waiting','conflict'], state: RecoveryState, reason: str)`; `reconcile_pending_effects(state: RecoveryState) -> ReconciliationResult` uses provider attempt journal and existing tool operation receipts. Resume uses the persisted cursor/context: bootstrap continues confirmed resource preparation, agent continues editing only in working/repairing, finalization resumes the existing proof checkpoint. It never repeats the original prompt/admission. Claim expiry alone does not prove the previous physical writer stopped: takeover also requires ownership/fence reconciliation.

- [ ] Add wait → coroutine exit → worker restart → two workers race; assert `status=='running'`, `finished_at is None`, `execution_started_at` unchanged, original prompt/admission called once, one continuation, previous side effect called once. Add pre-product bootstrap wait without a source artifact, cancellation/ownership loss at each await and `test_resume_rejects_changed_binding_without_writes`; missing source after a product write must refuse yield/release.

  ```python
  assert run.status == "running" and run.finished_at is None
  assert run.execution_started_at == original_started_at
  assert original_dispatch_calls == 1 and continuation_calls == 1 and business_effect_calls == 1
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_execution.py tests/test_generation_recovery_reconciliation.py`; RED: supervisor terminalizes yield, scanner treats it as orphan, or continuation repeats dispatch.
- [ ] Handle the typed yield before generic exceptions/finalization; reuse lifecycle cleanup but preserve scheduler-owned durable wait. Row lock/CAS and existing session advisory lock serialize attempt claims; fenced old workers cannot publish/checkpoint. Resume reconciles effects and reacquires normal capacity/admission without resetting prior paid-work accounting. A missing response stays pending; accounting alone never authorizes tool execution.
- [ ] Check owner/base snapshot/source/schema drift before applying the checkpoint. Retain sources and unresolved intent if storage/release is ambiguous; release Cell activity only with confirmed physical quiescence. Do not rollback arbitrary SQL or interpret finalization `resume` as arbitrary agent-action replay.
- [ ] Run new tests plus `test_generation_worker.py`, worker pool/concurrency, `test_generation_cancellation.py`, `test_generation_artifacts.py`, capacity/fence and source-checkpoint regressions on disposable PostgreSQL. Review/deliver; commit `feat(generation): resume durably yielded tasks without replay`. Activation still off.

### Task 10: Deployment drain and reader compatibility for waiting runs

**Files:** Create API `generation/recovery_quiescence.py`; modify `generation_deployment_drain.py`, worker heartbeat/scans, `infra/release/check-active-generations.sh`, `infra/release/deploy-prod.sh`, `infra/release/generation-publication-drain.py`; test `test_generation_recovery_quiescence.py`, existing drain/cutover/publication tests and `infra/release/test-release-tools.sh`.

**Interfaces:** `QuiescenceEvidence(run_id: UUID, checkpoint: CheckpointRef|None, attempt_seq: int, unresolved_operation_ids: tuple[UUID,...], drain_release_sha: str, reader_release_sha: str, no_physical_writer: bool, lease_released_or_fenced: bool)`; `quiesced_recovery_run(session: AsyncSession, *, run_id: UUID, release_sha: str) -> QuiescenceEvidence|None`. A missing checkpoint is admissible only for the independently proven no-product-work bootstrap case. `require_recovery_readers(session: AsyncSession, *, contract_version: int, target_release_sha: str) -> None` consumes expected live process inventory plus worker heartbeat capabilities and target release support. Add per-worker `omnia:health:generation-workers:{worker_id}` records with30-second TTL and recovery versions; preserve the old aggregate key for old consumers. One new worker's heartbeat cannot hide an older writer. Missing/stale/unregistered expected process capability is unknown, not compatible.

- [ ] Write concurrent tests: scheduler wake vs begin drain, missing checkpoint, running tool/gateway stream, unknown fence, old reader, unresolved external outcome with confirmed no physical writer. Include `test_unknown_contract_is_preserved_and_blocks_activation` and an old worker hidden by a newer aggregate heartbeat. Assert only verified `durably_quiesced` waits are excluded from execution blockers; unresolved intent remains recorded; one wake follows end-drain.

  ```python
  assert wake_calls_during_drain == 0 and unresolved_intent_still_recorded
  assert not unsafe_drain_status["drained"] and safe_drain_status["drained"]
  assert wake_calls_after_drain == 1
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_quiescence.py tests/test_generation_deployment_drain.py`; RED on wait blocking every safe deploy or false-green quiescence.
- [ ] Extend the same transaction/advisory drain used by admission to continuation claims. Make shell and DB gates share the verified classification; do not change the shell to ignore all running rows. Separate retained unknown external outcome from active physical writer; live gateway/tool/publication operations continue to block their cutover. No claim that the gateway has terminated based only on a missing heartbeat.
- [ ] Run release shell fixtures, exact gateway retirement/billing cutover tests and orchestrator publication drain tests. Test reader-first upgrade and compatible rollback with a saved waiter; an unsupported downgrade remains refused without destroying the task.
- [ ] Review/deliver; commit `feat(release): preserve quiesced recovery tasks across deploys`. Verify all participating services advertise v1 before any activation.

### Task 11: Soft work windows, independent repair phase and scheduler backoff

**Files:** Modify API `generation_deadline.py`, `max_finalization.py`, `generation/recovery_execution.py`, `generation/recovery_contract.py`, `core/config.py`; test `test_generation_recovery_deadlines.py`, existing deadline/progress/cleanup tests.

**Interfaces:** `recovery_deadline(state: RecoveryState, *, now: datetime, stage_budget_seconds: int) -> RecoveryDirective`; `account_active_work(state: RecoveryState, *, elapsed_ms: int) -> RecoveryState`; `next_dependency_check(run_id: UUID, *, failures: int, now: datetime) -> datetime`. For v1 `MAX_GENERATION_DEADLINE_SECONDS` (default1500) becomes a soft active segment window, with existing300-second finalization reserve; repair uses a separate active window of the same configured duration. Proof keeps existing controller timeouts and sealed adaptation semantics. Legacy and existing adaptation clocks stay unchanged.

- [ ] Add productive late compiler error → yield → independent repair → full proof; waiting time excluded from active segment clock, but previous spend/work totals never erased. Add `test_clock_jump_and_resume_do_not_restore_spend_allowance`, cancellation and terminal resource-policy boundary cases; assert last accepted version remains distinct from current incomplete work.

  ```python
  assert soft_boundary.action == "yield" and legacy_run.status == "failed"
  assert resumed.active_work_ms >= before_wait.active_work_ms
  assert spent_after_resume == spent_before_resume and repair_has_fresh_window
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_deadlines.py tests/test_generation_deadline.py`; RED: productive v1 is terminalized or resume replenishes spending allowance.
- [ ] Route v1 soft boundaries through durable yield, not through the ordinary terminal watchdog. Dependency polling uses15/30/60/120/300-second backoff with deterministic run jitter up to10%; scope and single-flight free health checks by actual provider credential/account. A catalogue200 alone is not proof of available balance or a successful generation. Long waiting does not hold a worker slot or poll the model with paid requests.
- [ ] Run deadline/finalization progress/worker fairness and cleanup fault regressions. Verify legacy1500 limit still holds, no v1 wait is abandoned after its invocation watchdog exits, and stop/cancel applies during every phase.
- [ ] Review/deliver; commit `feat(generation): checkpoint productive work at soft deadlines`. Full v1 activation remains Task15.

### Task 12: Durable user status, concrete progress and honest preview

**Files:** Create `apps/web/src/lib/generation-recovery.ts`; modify web `lib/api/types.ts`, `lib/generation-event-step.ts`, `lib/generation-lifecycle.ts`, `hooks/usePromptStream.ts`, `components/workspace/AgentTranscript.tsx`, `ChatMessage.tsx`, `components/max/MaxLivePreview.tsx`; API `schemas/message.py`, `routers/messages.py`, `generation/progress.py`; extend existing web durable-status/replay/preview tests.

**Interfaces:** API `GenerationRunPublic` exposes owner-authenticated optional `recovery:{version:1,phase,last_verified_action,reason,next_check_at,attempt_seq}` without body/secret data. TypeScript `GenerationRecoveryView` mirrors those fields (`phase` is the Task2 literal union; action/reason/ISO timestamp are string|null; sequence is number). Export `generationRecoveryLabel(value: GenerationRecoveryView): string` and `acceptedPreviewState(input: {acceptedSnapshotId: Uuid|null; acceptedAt: string|null; currentRun: GenerationRun|null}): AcceptedPreviewState`. `AcceptedPreviewState` contains kind `preparing|accepted`, snapshotId, acceptedAt, pendingRunId and isCurrentRequestComplete; the last flag requires the current run's existing validated readiness binding. Existing project/message/run/sequence binding remains mandatory.

- [ ] Add `generation-recovery.test.ts` plus render tests: reload/reconnect/reordered events, waiting and reconciliation, no first accepted snapshot, last accepted preview plus incomplete current run, foreign run event and unknown subphase. Assert exact waiting copy, next action, preserved preview forms, no false ready badge, no unrelated run hijack.

  ```typescript
  expect(screen.getByText("Работа сохранена, ожидаю восстановления")).toBeVisible();
  expect(preview.isCurrentRequestComplete).toBe(false);
  expect(startGeneration).not.toHaveBeenCalled();
  ```
- [ ] Run `pnpm test src/lib/__tests__/generation-recovery.test.ts` and the changed existing test files; RED on generic empty steps or a waiter shown as terminal/ready.
- [ ] Render “Работа сохранена, ожидаю восстановления” and concrete verified operation separately from heartbeat. Use durable REST truth after reload and existing WS replay/gap handling; UI never starts another generation merely to recover status. Keep owner/guest/MAX authorization unchanged.
- [ ] Run web typecheck/lint, durable lifecycle/replay/transcript/preview suites and build; API schema/status ownership tests. Exercise cancel while waiting and reopening the same project without paid calls.
- [ ] Review/deliver; commit `feat(web): show preserved generation progress and waiting state`, deploy web and API in normal sequence.

### Task 13: Gate completion on mandatory functions of the exact candidate

**Files:** Create API `generation/recovery_acceptance.py`; modify `generation/agent_finalization.py`, `max_finalization.py`, `max_behavior_proof.py`, `generation_runs.py`; test `test_generation_recovery_acceptance.py`, existing named behavior/browser/artifacts/finalization tests.

**Interfaces:** `check_requirement_acceptance(requirements: Sequence[RecoveryRequirement], witnesses: Sequence[RequirementWitness], *, binding: RecoveryBinding) -> AcceptanceVerdict`; `AcceptanceVerdict(ready: bool, missing: tuple[str,...], failed: tuple[str,...])`. Integrate as an additional gate before promotion/publication/completed for v1, while retaining every current proof and promotion permit. A model evaluator may propose reproducible checks; it cannot create RequirementWitness directly.

- [ ] Write green build + absent required action, changed source after PASS, old schema, forged witness, no true MAX identity, manual criterion without evidence, and complete valid contract cases. Assert missing/failed IDs, no snapshot publication or completed flag on incomplete requirements, and no gate bypass by a fallback summary.

  ```python
  assert not verdict.ready and required_action_id in verdict.missing
  assert publication_calls == 0 and run.status != "completed"
  assert all_current_verified_verdict.ready
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_acceptance.py`; RED on technically green but functionally incomplete acceptance.
- [ ] Execute supported HTTP/browser/DB/named checks against the sealed candidate and bind their receipts. Use separate synthetic fixtures for mutations, disable payments/external sends server-side in QA, test create/edit/restore persistence and trusted A/B identity. Unknown requirements remain explicit incomplete obligations; do not weaken them to make the dashboard green.
- [ ] Run actual Next behavior fixtures, publication no-op/stale-fence tests and ownership/schema/restore regressions on isolated DB. Test changed-prompt linked runs cannot use the prior run's answer/PASS.
- [ ] Review/deliver; commit `feat(generation): require functional acceptance before completion`. Gate ships off for legacy; v1 canary enabled only in Task15.

### Task 14: Fix and verify the inspector route; preserve real browser failures

**Files:** Create `infra/security/nginx/inspector-relay.conf.template`, `infra/release/verify-inspector-relay.py`; modify `apps/orchestrator/src/yleum_orchestrator/services/nginx_writer.py`, `core/config.py`, canonical deployment configuration in `infra/release/deploy-prod.sh`; API `routers/public.py`, `static/omnia-inspector.js` only for error capture; test `apps/orchestrator/tests/test_nginx_writer.py`, `apps/api/tests/test_behavior_platform_assets.py`, `apps/api/tests/test_select_mode.py` and new `apps/orchestrator/tests/test_inspector_upstream_contract.py`.

**Interfaces:** Keep `_inspector_asset_target() -> str` loopback-only validation and canonical API asset path. `_render_inspector_relay(origin: str) -> str` renders Commerce-only NGINX bound to127.0.0.1:8200, with one exact `/api/kit/omnia-inspector.js` location and404 elsewhere, forwarding to the configured canonical platform origin with TLS verification and correct SNI. Port ownership is checked before install; an unexpected listener blocks instead of being replaced. `verify-inspector-relay.py --expected-sha SHA` verifies status/type/content digest. Do not broaden the asset target validator into a general arbitrary upstream/SSRF surface or expose private API routes. API remains the canonical inspector source.

- [ ] Reproduce the documented refused localhost upstream in a local NGINX/service fixture. Assert the draft asset GET fails before fix and returns correct JS/content type/hash after fix; malicious upstream/path remains rejected. Browser error fixture must preserve error text, URL, stack and failed response, redacting secrets.

  ```python
  assert response.status_code == 200 and "javascript" in response.headers["content-type"]
  assert sha256(response.content).hexdigest() == canonical_asset_sha256
  assert unrelated_path.status_code == 404 and malicious_upstream_rejected
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_inspector_upstream_contract.py tests/test_nginx_writer.py` from orchestrator plus API asset tests; RED must show the actual route failure.
- [ ] Configure the local relay and narrowly update route rendering; use configured service names/addresses from deployment, not new hardcoded public IPs. Add relay readiness to vhost/release health. Inspectors report client exceptions without silently clearing historical error counters.
- [ ] Run asset, boundary, configured-origin and SSRF/security tests. On live Commerce confirm response status/type/hash and browser console/network on exact accepted preview. Attribute remaining console failures individually; this fix does not close every old error automatically.
- [ ] Review/deliver; commit `fix(preview): serve the canonical inspector through a healthy local relay`.

### Task 15: Fault matrix, controlled activation and the live acceptance campaign

**Files:** Create `apps/api/tests/test_generation_recovery_fault_matrix.py`, `apps/api/tests/test_generation_recovery_campaign.py`, `tools/qa/generation_recovery_campaign.py`, `docs/qa/2026-10-16-generation-recovery-checkpoint.md`; modify API `services/llm_client.py`, `core/config.py` only for the scoped diagnostic described below, `.github/workflows/ci.yml` and `ops/production_smoke.py`. Reuse `tools/qa/core_owned_generation_probe.py` and restoration QA safety bindings; do not run legacy release runners as test helpers.

**Interfaces:** `run_campaign(*, checkpoint_path: Path, expected_release: str, project_ids: tuple[UUID,UUID], execute: bool=False) -> dict[str,object]` and `main(argv: Sequence[str]|None=None) -> int` in the new tool. CLI requires `--checkpoint PATH --expected-release SHA --project-a UUID --project-b UUID`; default is read-only, `--execute` explicitly admits generations through the normal owner API and existing private session profile, never by invoking `execute_dispatch` directly. Verify current owner/bot/environment before writes. Checkpoint stores source/service revisions, run/attempt/provider IDs, requirement results, data hashes/owners, costs known/unknown, accepted rounds and blocker; no credentials/full bodies. API `diagnostic_output_budget(*, run_id: UUID, attempt_id: UUID, requested_max: int|None) -> int|None` normally returns the original value. A signed operator-only canary intent, persisted before normal dispatch and bound to exact run/project/first attempt, may return16 once. Require modecanary, allowlisted isolated project, normal admission and purpose-specific signature; a browser field alone cannot activate it. Intent consumption is durable/idempotent, cannot target a later attempt, and does not mutate model defaults.

- [ ] Add fault-matrix tests for the spec's15 scenarios, both loop paths and cancellation boundaries. Include process termination at before-dispatch, after-dispatch and after-store checkpoints. Add campaign tests: default performs zero POSTs, wrong revision/owner refuses writes, forged diagnostic rejected, signed first-attempt override consumed once, every later ordinary Sonnet request omits max. Assert one allowed physical effect, immutable accepted data, preserved pending state, no old-source success and refusal of forged identity/receipt. Put these suites on disposable PostgreSQL/Redis/private object storage in exact-revision CI.

  ```python
  assert provider_posts_per_attempt <= 1 and business_posts_per_operation <= 1
  assert accepted_rows_and_owners_after == accepted_rows_and_owners_before
  assert not stale_source_completed and not forged_receipt_accepted
  ```
- [ ] Run `uv run --frozen pytest -o addopts='' -q tests/test_generation_recovery_campaign.py` before implementation; RED on missing read-only/scope guarantees, not unavailable production access. Implement the campaign collector/normal API admission, checkpoint and the operator-only canary intent, with no alternative worker dispatcher or fabricated finish reason.
- [ ] Run new matrix/campaign tests plus all focused suites from Tasks1–14; GREEN only with no skipped mandatory DB/ownership/cancel/drain assertions. Validate migrations on a restored copy, all earlier rows and receipts, reader capability manifest, private retention/cleanup and existing billing contracts. Full required CI must pass.
- [ ] Complete independent whole-branch review; resolve findings. Deploy readers and schema with modeoff, verify exact service revisions including gateway, then shadow with zero additional model calls. Publish capability/readiness evidence; unknown/stale reader keeps canary disabled.
- [ ] Set canary only for explicit isolated project IDs, using normal worker daemon, normal admission/fencing and configured actual model. First close one smallest creation/edit/recovery flow; no private diagnostic dispatcher or forged response may stand in for ordinary-worker acceptance. A controlled truncation test must be a scoped diagnostic mode applied to the canary's ordinary gateway attempt with actual response receipts; afterward prove ordinary omission restored and require no global output-cap change.
- [ ] Run four consecutive rounds of two parallel generations on the same verified release. AppA uses the currently authorized amoCRM-linked bot only after owner/project/binding revalidation; preserve its current binding, no transfer to the old coffee project. AppB is tested without a bot. Use synthetic business data with no payments/external sends; test required actions, comment/history/repeat where requested, reload, version change, full owned-row preservation and direct foreign CRUD denial with two actual authorized identities. Foreign GET/PATCH/DELETE must return the existing forbidden/not-found contract (403/404), no foreign content and no changed owner-row hash. Test390/768/1440px layouts with actual click/scroll/focus reachability, empty/loading/error states, computed fonts and all unexpected network/console failures. If a second MAX identity/access is unavailable, report that acceptance case blocked rather than synthesize it.
- [ ] After each failure save full causal evidence, repair the smallest proven cause through the same task/review/delivery loop, and restart the four-round counter on the new revision. Keep successful intermediate artifacts; old-run results do not count toward the new cohort. Distinguish known funds failure from unresolved429; reconcile costs or leave them unknown.
- [ ] Accept only if all mandatory requirement/proof/data cases pass, no unresolved console/network defect remains, old preview is never presented as current completion, cancellation/wait/redeploy/resume work, and final wire shows default Sonnet budget omission. Compare first-pass/repaired acceptance, time, calls and cost with baseline; no100% statistical claim from eight runs. Enable modeon only after this recorded gate. Roll back activation by stopping new v1 admissions while compatible readers reconcile existing tasks; never downgrade over unconsumed v1 state.
- [ ] Review/deliver final checkpoint and campaign tooling; commit `test(generation): verify durable recovery and live acceptance`. Record exact GitHub/production revisions, health and evidence locations.

## Self-review and handoff

Coverage map: spec§1 → Tasks2/3/7/9/10; §2 →4/5; §3 →7/8/9; §4 →9/10/11/12/13; §5 →1/13/14/15; compatibility/delivery →2/7/10/15. Each Review Focus input above is pinned to a named owning task. Existing v0 and adaptation policy remain separate from v1 until controlled activation.

Before implementation, refresh exact base/head and available Alembic revision, inspect each named file at that revision, and preserve unrelated changes. Run this plan task-by-task; do not build every module in one unreviewable change. Windows-local unit passes do not replace Linux service-backed CI or live deployment.

Current external blocker: Core SSH closes before commands; fallback routes timed out in the preceding audit. Public API returned HTTP200 on runtime revision `2c2b9b81`; this is not evidence that the recovery implementation exists or has been deployed. Resume the outstanding documentation delivery loop when CI/access permit; lack of access does not authorize a production bypass.

Plan saved for user review. Recommended execution: a fresh implementer and independent reviewer for each task, then whole-branch review; this suits the coupled identity, billing, cancellation and deployment changes. The user must confirm this plan and execution choice before product implementation.
