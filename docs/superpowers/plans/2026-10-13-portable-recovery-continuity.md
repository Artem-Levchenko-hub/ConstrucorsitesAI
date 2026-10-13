# Portable Recovery Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сохранить последние подтверждённые записи при потере всей тройки, переключать новые роли за измеренные несколько минут и добавлять мощности через тот же профиль.

**Architecture:** Непрерывная независимая долговечность дополняет полный K-комплект и R-runner. Общий writer fence, стабильный внешний ingress и автомат переключения закрывают split brain и stale rollback. Scale использует тот же discovery/bootstrap, отдельный ресурсный gate и канонический deployment.

**Tech Stack:** Existing PostgreSQL physical basebackup/WAL/standby protocols, versioned object storage/change journal, existing API/runtime writer paths, independent CAS/ingress adapters; Python/pytest recovery package K/R-планов.

**Spec:** [Согласованная схема](../specs/2026-10-13-portable-recovery-design.md); [порядок](2026-10-13-portable-recovery.md); prerequisites [K](2026-10-13-portable-recovery-kit.md), [R](2026-10-13-portable-recovery-runner.md).

## Global Constraints

- Подтверждённые бизнес-записи терять нельзя; независимая долговечность проверяется **до** успешного ответа клиенту.
- Финальное плановое окно начинается с первого закрытого писателя и ограничено 300 секундами.
- Генерация/MAX QA проверяются заранее в изолированной среде; QA записи не переносятся в production.
- Все затронутые сайт/API/MAX/wildcard/dev/dev2/webhook/TLS routes используют стабильный вход вне тройки.
- После первой новой записи stale rollback запрещён; старые писатели остаются ограждёнными.
- Стоимость/production activation/тестовые мощности отдельно авторизуются; сейчас только GitHub.

## Review Focus

1. External WAL/object store недоступен во время commit: нет ложного подтверждения или повторной транзакции — C1.
2. Авторизация/callback/runtime SQL пишет при закрытой generation admission: общий fence закрывает запись — C2.
3. Ingress API timeout после реального переключения: читать фактическую routing revision до повтора — C3.
4. Истечение 300 секунд после promotion/первой новой записи: нельзя открыть stale old copy — C3.
5. Добавление worker увеличивает pool budget/rolling overlap сверх живого PostgreSQL лимита: отказ до регистрации — C4.

## Структура и interfaces

Модули recovery в `infra/recovery/src/yleum_recovery/`; per-role remote helpers в `infra/recovery/remote/`.
`durability.py` определяет `DurabilityProof(operation_id, db_watermarks, object_versions, file_sequences, external_ack)`
и `DurableBoundary(point_id, per_component, cutoff, completeness)`.
`writers.py` определяет полный `WriterInventory` и `WriterFenceProof(epoch, closed_components, drained, boundary)`.
`cutover.py`: состояния prepared/frozen/promoted/routed/open/aborted/recovery_required,
монотонный таймер, `first_new_write: bool` отдельно от routing.
`IngressAdapter.read_revision() -> RoutingState`; `IngressAdapter.switch(expected_revision: str, topology: Topology) -> RoutingState`.
`connection_budget.py`: `ProcessBudget`, `DatabaseCapacity`, `BudgetProof` для каждого DB endpoint.
Короткие module paths ниже относительны package K-плана; `tests/` — `infra/recovery/tests/`.
Production consumers используют versioned JSON/собственные guarded dispatchers,
а не импортируют административный CLI или весь recovery package в API/runtime.

### C1: Совместимая непрерывная внешняя копия

**Files:** Create `durability.py`, `remote/physical-backup.py`, `tests/test_durability.py`; Modify `exporters.py`, `restore_database.py`, `infra/max-k3s/remote/40-postgres.sh`, `apps/api/src/yleum_api/core/minio.py`, `services/generation/source_checkpoint.py`, `apps/orchestrator/src/yleum_orchestrator/services/cell_checkpoint.py`; остальные authoritative handlers дополняют checked inventory C2 до certification.
**Interfaces:** `capture_boundary(inventory: Inventory) -> DurableBoundary`; `verify_durable_operation(proof: DurabilityProof) -> Proof`; `replay_database(base: Component, wal: tuple[Component, ...], boundary: DurableBoundary, target: Target) -> StepProof`.

- [ ] Tests basebackup+same-major WAL ⇒ exact sentinel; pg_dump+WAL ⇒ reject; gap/timeline mismatch/object version missing ⇒ `ok is False`; external failure ⇒ client success absent; commit outcome unknown ⇒ operation ID preserved/no blind replay.
- [ ] `uv run --frozen pytest -q tests/test_durability.py` ⇒ FAIL; PostgreSQL fixture использует disposable primary и независимый standby, не production.
- [ ] Реализовать physical basebackup+continuous WAL для **каждого** авторитетного DB cluster из inventory, совместимую major/timeline цепочку. Snapshot logical mode сохранить отдельно. Первый zero-ack-loss backend — независимый synchronous standby с `synchronous_commit=on` и доказанным remote flush; asynchronous archive alone не достаточен. Отключение sync через роль/соединение должно обнаруживаться и блокировать readiness. Objects/file mutations сохранять versioned content и operation journal вне тройки до acknowledgement; manifest связывает database LSN, object versions и file sequences. При утрате внешней долговечности не переходить молча к asynchronous success. Сверять semantics по [PITR](https://www.postgresql.org/docs/current/continuous-archiving.html) и [synchronous standby](https://www.postgresql.org/docs/current/warm-standby.html) для фактической сохранённой PostgreSQL major.
- [ ] Pytest ⇒ PASS; integration fixture выключает обе локальные исходные БД после confirmed commit и импортирует только external state: последний ack sentinel присутствует, unacknowledged outcome классифицирован отдельно. Повторить для объекта+DB relation и приватного файла/checkpoint.
- [ ] Commit: `feat: preserve externally durable recovery boundaries`.

### C2: Полный writer inventory и ограждение

**Files:** Create `writers.py`, `tests/test_writer_fence.py`, `docs/ops/recovery-writer-inventory.md`, `apps/api/src/yleum_api/services/recovery_write_guard.py`, `apps/api/tests/test_recovery_write_guard.py`; Modify `capture.py`, `infra/release/deploy-prod.sh`, `generation-ingress-drain.py`, `generation-publication-drain.py`, `apps/api/src/yleum_api/main.py`, `workers/run.py`, `workers/generation.py`, `workers/billing.py`, `services/generation_deployment_drain.py`. Runtime consumers — `apps/orchestrator/templates/max-miniapp-nextjs/src/app/api/omnia/{actions,events,consents}/route.ts`, `api/max/{session,webhook}/route.ts`, `api/omnia/integrations/[...path]/route.ts`; shared runtime guard создать в `src/lib/recovery-write-guard.ts`, tests в `scripts/public-max-core/recovery-fence.test.cjs`. Пути API относительны `apps/api/src/yleum_api`, пути runtime — соответствующим template root.
**Interfaces:** `enumerate_writers(topology: Topology) -> WriterInventory`; `freeze_all(inventory: WriterInventory, epoch: int, deadline: float) -> WriterFenceProof`; `assert_writer_epoch(component: str, epoch: int) -> None`; `acknowledge_operation(proof: DurabilityProof, epoch: int) -> Proof`.

- [ ] Tests закрывают business API/runtime SQL, auth write, callbacks, scheduler, billing, integrations и project files; `assert admitted_writes == 0`; in-flight операции drain до boundary; unknown writer ⇒ отказ; old epoch ⇒ denied; external durable proof отсутствует ⇒ success не выдан.
- [ ] `uv run --frozen pytest -q tests/test_writer_fence.py` ⇒ FAIL. Добавить disposable DB/runtime integration tests для реальных dispatch points, не только mocked middleware.
- [ ] Сначала сохранить checked writer map по всем mutation entrypoints текущего code/release/config, включая routes вложенных actions, file/object handlers и дополнительные scheduler units из inventory; каждый mapped consumer получает конкретный path/test в этом документе перед его изменением. Подключить единый fence к каждому consumer и acknowledgement path C1; в динамических PG clusters запретить обход middleware прямым старым соединением через DB/provider fencing. Закрыть admission, drain текущие эффекты, снять общий final boundary. Переиспользовать K4 capture proof; generation-only drain не переименовывать в all-writer gate.
- [ ] New tests ⇒ PASS; API regressions `uv run --frozen pytest -q tests/test_generation_deployment_drain.py tests/test_generation_worker.py`; orchestrator `uv run --frozen pytest -q tests/test_publication_deployment_drain.py`; fixture старого callback/DB connection не может писать после смены epoch.
- [ ] Commit: `feat: fence every authoritative writer for recovery`.

### C3: Стабильный вход и автомат планового переключения

**Files:** Create `ingress.py`, `cutover.py`, `tests/test_cutover.py`, `tests/test_ingress_contract.py`; Modify `runner.py`, `receipt.py`, versioned public-route templates.
**Interfaces:** `prepare_cutover(point: RestorePoint, targets: TargetSet, qa: QAScope) -> PreparedCutover`; `execute_cutover(prepared: PreparedCutover, clock: Clock) -> RecoveryReceipt`; `safe_abort(state: CutoverState) -> AbortProof`. Types/Clock defined in `cutover.py`; `PreparedCutover` связывает QA proof, source/target IDs, ingress revision и expected boundary.

- [ ] Tests clock elapsed 300s до promotion ⇒ source reopened только после target fence proof; first_new_write=True ⇒ stale rollback denied; routing timeout ⇒ read revision/no duplicate switch; missing wildcard/MAX route/TLS proof ⇒ no open; QA records отсутствуют в final inventory.
- [ ] `uv run --frozen pytest -q tests/test_cutover.py tests/test_ingress_contract.py` ⇒ FAIL.
- [ ] Реализовать подтверждённый stable ingress adapter вне тройки. Поддерживать весь route inventory, TLS и webhook identities; provider-specific backend switch с compare-and-set/read-back. До окна: preseed+долгие QA. В окне: freeze всех писателей → final delta/replay+checks → provider fence старой группы → promotion/route → open. Deadline=first freeze+300s; safe_abort доказывает актуальность old state и возвращённое ownership до reopening. После новой записи только forward recovery либо отдельная verified reverse sync. Cold recovery меряется отдельно.
- [ ] Pytest ⇒ PASS; authorized planned drill с синтетическими бизнес-данными, реальными routing/TLS/MAX proofs и измерением из первого freeze. Превышение бюджета не считается успехом; actual <=300s требуется до certification.
- [ ] Commit: `feat: switch all public routes within a guarded cutover window`.

### C4: Добавление cell-hosts и общий PostgreSQL budget

**Files:** Create `connection_budget.py`, `scale.py`, `tests/test_connection_budget.py`, `tests/test_scale.py`; Modify `infra/release/deploy-prod.sh`, `apps/api/src/yleum_api/services/orchestrator_hosts.py`, `orchestrator_placement.py` только для validated registry update.
**Interfaces:** `check_connection_budget(processes: tuple[ProcessBudget, ...], capacities: tuple[DatabaseCapacity, ...]) -> BudgetProof`; `add_cell_hosts(profile: RecoveryProfile, selector: TargetSelector) -> ScaleReceipt`. `ScaleReceipt` в `scale.py` содержит new IDs, unchanged bindings, config/release proofs, budget verdict.

- [ ] Tests total includes API/generation/lifecycle/billing/gateway/maintenance/rolling overlap; 20 per API-family process и 10 gateway берутся из фактических configs, не vCPU; превышение live capacity ⇒ no registration; old project bindings неизменны; unknown reserve ⇒ preflight fail.
- [ ] `uv run --frozen pytest -q tests/test_connection_budget.py tests/test_scale.py` ⇒ FAIL.
- [ ] Реализовать budget по каждому фактическому DB endpoint и `pg_settings`, учитывая `max_connections`, superuser/reserved connections при наличии и explicit maintenance reserve. Использовать R discovery/bootstrap/readiness, регистрировать новые logical hosts через canonical compose+hostdb+drain. Новые проекты получают мощность; существующие не перемещать этой командой. Gate вызывается перед apply/scale/reopen и canonical deploy. Plain compose из old order helper не переносить.
- [ ] New pytest ⇒ PASS; API regressions `uv run --frozen pytest -q tests/test_orchestrator_hosts.py tests/test_generation_worker_pool_budget.py tests/test_generation_worker_concurrency.py tests/test_generation_worker_host_fairness.py`; saturated disposable PG сохраняет reserve для control/cancel, rejected scale не меняет registry.
- [ ] Commit: `feat: add portable capacity with a global database budget`.

### C5: Приёмка полного комплекта и инструкция агенту

**Files:** Create `drill.py`, `tests/test_drill_verdict.py`, `docs/ops/portable-recovery-acceptance.md`; Modify `docs/ops/portable-recovery.md`, `AGENTS.md` recovery entrypoint, CI recovery job и certification index K3.
**Interfaces:** `certify_recovery(receipt: RecoveryReceipt, durability: Proof, cutover: RecoveryReceipt, reboot: Proof) -> Proof`; CLI `yleum recover --profile cortel`, `yleum verify --profile cortel`, `yleum scale --profile cortel` используют единый local profile.

- [ ] Tests health-only/пустая QA/старый release proof ⇒ not certified; missing latest ack/custom volume/checkpoint ⇒ not certified; incomplete required action count ⇒ not certified; all exact release/data/MAX/reboot proofs ⇒ certified.
- [ ] `uv run --frozen pytest -q tests/test_drill_verdict.py` ⇒ FAIL.
- [ ] Реализовать machine-readable verdict с full SHAs/digests/config/boundary/run IDs, функциональными proofs и раздельными RPO/RTO/cutover измерениями. Инструкция «подтяни» обновляет trusted recovery tool, но выбирает compatible certified release из point. Синтетические examples, zero-secret public receipts; full logs приватны и redacted summaries сохраняются в Git.
- [ ] Из `infra/recovery`: `uv run --frozen pytest -q`, lint/types; shell gates `bash infra/release/test-release-tools.sh`, `bash infra/release/test-compose-policy.sh`. Полное authorized учение: старые VPS и registry недоступны, второй ПК, three blank targets, latest ack preserved, login/preview/generation/cancel/restore/MAX/integration flow, role reboot, crash/resume, stale controller и no duplicate external actions. Все обязательные proofs PASS, cutover <=300s; холодный RTO измерен.
- [ ] Commit: `feat: certify portable recovery through complete disaster drills`; push, exact CI, independent review; production activation только после явного разрешения с backup/rollback/health evidence.

## Завершение

`RECOVERY_READY` допускается лишь после C5 с реальными adapters/полным exported
state и долговечностью всех authoritative writes. Наличие файлов в GitHub, зелёных
unit tests или успешного health не заменяет это учение. В текущем scope готовим
код и проверки; реальные услуги/экспорт/production/платные QA ждут соответствующего
разрешения, а не выполняются из-за самого наличия плана.
