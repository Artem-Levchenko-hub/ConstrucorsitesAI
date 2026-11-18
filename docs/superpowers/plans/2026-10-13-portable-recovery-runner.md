# Portable Recovery Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Восстанавливать текущие три роли одной командой на автоматически обнаруженных мощностях с безопасным продолжением после обрыва.

**Architecture:** Runner потребляет комплект K-плана и сохраняет состояние вне компьютера и трёх целей. Provider/SSH/storage/lease adapters отделены от renderer и restore. Сохраняются логические host IDs; физические адреса и инфраструктурные credentials создаются для новых целей.

**Tech Stack:** Standalone Python 3.12/uv package K-плана, asyncssh/httpx, pytest, versioned shell/templates для Linux x86_64; текущие Docker/K3s/PostgreSQL и canonical compose/hostdb.

**Spec:** [Согласованная схема](../specs/2026-10-13-portable-recovery-design.md); [порядок](2026-10-13-portable-recovery.md); prerequisite [K-план](2026-10-13-portable-recovery-kit.md).

## Global Constraints

- `yleum recover --profile cortel`; пользовательских IP-параметров нет; новые provider IDs отделены от logical host IDs.
- Новые цели пустые; nonempty resume разрешён только для того же recovery run ID и закреплённых машин.
- Несколько подходящих групп/неподтверждённые API/SSH trust дают отказ до изменения целей; покупки и delete API не входят в adapter.
- Старые писатели и просроченный исполнитель ограждаются фактически, не одной локальной блокировкой.
- Restore использует полный совместимый certified release; никаких fresh build/latest main и удаления project workspaces.
- Logical snapshot mode — тестовый промежуточный результат с указанным RPO; полная production readiness требует C-плана.

## Review Focus

1. Две группы с одинаковыми названиями, чужая машина, IPv6-only или stale instance ID: discovery однозначен либо отказ до effects — R1.
2. Обрыв между effect и записью квитанции: probe результата предотвращает повтор эффекта — R2.
3. Два компьютера/истёкший lease/старый SSH command: только ограждённый новый исполнитель продолжает — R2/R3.
4. Другой NIC/CIDR/MTU или недостаточный диск: renderer/preflight не использует старые literals — R3.
5. Готовая сборка, но неверные данные/владелец/MAX-привязка: CLI не выдаёт readiness по health alone — R4/R5.

## Структура и общие interfaces

Новые модули в `infra/recovery/src/yleum_recovery/`, тесты в `infra/recovery/tests/`.
`targets.py` определяет `Target(instance_id, provider, role, endpoints, resources, ssh_host_key)`
и `TargetSet`; `TargetSelector(provider, account_id, group_id, role_instance_ids)`
содержит только новые target IDs/метки из профиля, без адресов.
`topology.py` — `Topology`, сохраняющую logical host names и discovered mapping.
`journal.py` — `RecoveryJournal(run_id, target_ids, point_id, epoch, steps, revision)`;
`LeaseToken(run_id, holder_id, epoch, expires_at, revision)`; `StepProof(step, input_hash, output_hash, result)`.
`transport.py` protocol `run(target: Target, operation: RemoteOperation, lease: LeaseToken) -> RemoteProof`
не принимает произвольную shell-строку пользователя; typed operations принадлежат trusted release.
`RemoteOperation` содержит ID, trusted artifact/hash, private stdin refs и non-secret arguments.
Все модели определять в названном модуле и экспортировать через package, без импорта production apps.
Короткие module paths ниже относительны package, `tests/` — `infra/recovery/tests/`,
`remote/` и `templates/` — `infra/recovery/`. `RecoveryStep`/transport protocols
создаются в R2 до конкретного SSH backend R3, чтобы границы не ссылались на отсутствующий модуль.

### R1: Подтверждённый discovery выданных серверов

**Files:** Create `targets.py`, `providers/base.py`, `providers/serverum.py`, `tests/test_discovery.py`; читать existing `apps/orchestrator/src/yleum_orchestrator/services/serverum.py`, `serverum_cli.py`, `tests/test_serverum.py` как prototype evidence. `HostIdentity` и `FenceProof` определить в `providers/base.py`.
**Interfaces:** `Provider.list_targets(selector: TargetSelector) -> TargetSet`; `Provider.confirm_host_identity(target: Target) -> HostIdentity`; `Provider.fence_instances(ids: tuple[str, ...]) -> FenceProof`.

- [ ] Tests с recorded/synthetic responses: ровно три роли selected group ⇒ 3 targets; ambiguous/occupied/stale ID/untrusted key ⇒ zero mutations; `assert not hasattr(adapter, 'order')`; IPv6-only ⇒ explicit unsupported до effect, пока transport не проверен.
- [ ] `uv run --frozen pytest -q tests/test_discovery.py` ⇒ FAIL.
- [ ] Реализовать read-only adapter по реальному подтверждённому контракту. API paths из prototype не считать рабочими: сначала получить authorized read evidence/официальный контракт, сохранить redacted fixtures и capability proof с version. SSH keys сверять через trusted provider channel; неподдерживаемое hardware fencing запрещает promotion. Ресурсы и IDs обнаруживаются, пользователь их IP не вводит.
- [ ] Новый pytest ⇒ PASS; из `apps/orchestrator` `uv run --frozen pytest -q tests/test_serverum.py`; live discovery разрешённой тестовой группы без order/delete обязан совпасть с кабинетом.
- [ ] Commit: `feat: discover recovery targets through verified provider adapters`.

### R2: Внешний journal, lease и restart со второго ПК

**Files:** Create `journal.py`, `lease.py`, `runner.py`, `transport.py` с protocols, `tests/test_resume.py`.
**Interfaces:** `LeaseStore.read(key: str) -> VersionedRecord`; `LeaseStore.compare_and_set(key: str, expected_revision: str | None, value: bytes) -> str`; `begin_or_resume(profile: RecoveryProfile, targets: TargetSet, point: RestorePoint) -> RecoveryJournal`; `advance(journal: RecoveryJournal, step: RecoveryStep, transport: Transport) -> RecoveryJournal`. `VersionedRecord(data, revision, server_time)` определён в `lease.py`; lease TTL=60s, renewal каждые 15s по доверенному server time. `RecoveryStep` enum: preflight/bootstrap/import/fence/verify/route/open; `Transport` — protocol R-плана.

- [ ] Tests crash до/после каждого effect: `assert effect_count == 1`; второй controller до fenced takeover ⇒ denied; epoch_old после takeover ⇒ denied; изменённый instance ID ⇒ `TARGET_MISMATCH`; occupied marker другого run ⇒ denied.
- [ ] `uv run --frozen pytest -q tests/test_resume.py` ⇒ FAIL.
- [ ] Реализовать durable CAS journal с монотонным epoch и server-side revision/clock; takeover только после fence proof прежнего исполнителя/писателей. Перед каждым effect сверять lease, после неопределённого outcome — фактический remote probe, не blind replay. Adapter обязан пройти atomic conditional-write contract; filesystem/in-memory fallback разрешён только в tests. Для S3 backend см. [conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html); совместимость другого storage проверяется отдельно. Отсутствие доступа к coordinator закрывает mutations.
- [ ] Pytest ⇒ PASS; contract race test двух процессов на реальном независимом coordinator ⇒ ровно один победитель и монотонные revisions; provider fencing прекращает старый delayed command до допуска нового.
- [ ] Commit: `feat: resume recovery with external journal and fencing`.

### R3: Renderer и идемпотентная подготовка ролей

**Files:** Create `topology.py`, `bootstrap.py`, `templates/`, `remote/probe-target.py`, `tests/test_topology.py`, `tests/test_bootstrap.py`; Modify `transport.py` с SSH backend, pyproject/lock для asyncssh и нужные primitives `infra/max-k3s/remote/{00-bootstrap,10-wireguard,20-k3s,40-postgres,70-gvisor}.sh` и `cells/10-cell-host-prep.sh` только после live config export.
**Interfaces:** `render_topology(targets: TargetSet, release: ReleaseManifest, config: EffectiveConfig) -> Topology`; `prepare_role(target: Target, topology: Topology, lease: LeaseToken) -> StepProof`. `EffectiveConfig` в `topology.py` содержит non-secret schema/versions/resource budgets и secret refs K-плана.

- [ ] Tests old-IP/NIC отсутствуют в rendered config; перекрытие CIDR/disk shortage ⇒ no apply; повтор bootstrap ⇒ одинаковые hashes/IDs; production compose включает hostdb; old K3s node identity не появляется в новой цели.
- [ ] `uv run --frozen pytest -q tests/test_topology.py tests/test_bootstrap.py` ⇒ FAIL.
- [ ] Реализовать probes NIC/MTU/routes/disk/OS/arch и единый renderer SSH aliases, service names, private overlay, PG/firewall, compose, nginx/public routes. Перенести только проверенные реальные private env/nginx helpers в templates без секретов; fresh K3s/kubeconfig/certs, retained app keys. Transport: strict SSH, typed parameters/private stdin, atomic 0600 writes, dependency artifact hashes. Plain legacy provision/migrate/order helper не вызывать. Все длинные remote effects имеют отмену и доказанный fence перед takeover.
- [ ] Новые pytest ⇒ PASS; `bash infra/project-cell/tests/test-phase2-tools.sh`, `bash infra/release/test-compose-policy.sh`; exit 77 означает missing prerequisite, не PASS. На disposable targets два apply дают одинаковую конфигурацию и работают после reboot.
- [ ] Commit: `feat: render and bootstrap portable three-role targets`.

### R4: Импорт полного состояния с проверкой границы

**Files:** Create `restore.py`, `restore_database.py`, `restore_volumes.py`, `tests/test_restore.py`; читать existing `cell_checkpoint.py`, `machine_environment.py`, `cell_state.py` и database credential handlers.
**Interfaces:** `restore_point(point: RestorePoint, topology: Topology, journal: RecoveryJournal) -> StepProof`; `verify_restored_state(point: RestorePoint, topology: Topology) -> Proof`.

- [ ] Tests missing volume/key/hash ⇒ zero imports; correct snapshot ⇒ sentinel/owners/host bindings/checkpoint сохранены; logical dump + WAL ⇒ reject; physical point без непрерывной WAL-chain ⇒ reject; repeated import после crash не стирает подтверждённые данные.
- [ ] `uv run --frozen pytest -q tests/test_restore.py` ⇒ FAIL.
- [ ] Реализовать проверку всех manifests/архивов до import, DB/globals/objects/volumes/journals restore на ограждённых целях. Logical importer восстанавливает точный snapshot; physical mode отвергается как `BACKEND_UNAVAILABLE` до C1. После C1 его importer применяет совместимую basebackup/WAL chain до durable boundary. Воссоздать ресурсы K3s/PVC с logical bindings без старых physical IDs. Смонтировать volumes по данным inventory, сверить retained crypto modes, снять orphan flags только штатной процедурой; callbacks/billing не включать.
- [ ] Pytest ⇒ PASS; из `apps/orchestrator` `uv run --frozen pytest -q tests/test_cell_checkpoint.py tests/test_project_database_credentials.py tests/test_recovery_resume.py`; plaintext sibling archives недоступны, ciphertext-only import на новых целях сохраняет все sentinel values.
- [ ] Commit: `feat: restore complete logical state on fenced targets`.

### R5: Одна команда, функциональная квитанция и cold drill

**Files:** Create `cli.py`, `verify.py`, `receipt.py`, `tests/test_recover_cli.py`, `tests/test_functional_gate.py`, `tools/recovery/yleum.ps1`; Modify `docs/ops/portable-recovery.md`, pyproject entrypoint `yleum=yleum_recovery.cli:main`.
**Interfaces:** `recover(profile_name: str, adapters: AdapterSet) -> RecoveryReceipt`; `verify_functions(topology: Topology, qa: QAScope) -> Proof`. `AdapterSet` в `runner.py` содержит provider/transport/store/lease/ingress; `QAScope` в `verify.py` — test identities, approved external actions/budget. `RecoveryReceipt` содержит run/release/point/target IDs, steps/proofs, RPO, cold duration, cutover duration, `ready: bool`, `verdict: str` без credentials.

- [ ] Tests exact command принимает profile без IP; missing function/owner mismatch/MAX mismatch ⇒ `ready is False`; неопределённый paid outcome ⇒ `resume_required`, external_count не растёт; simple health alone ⇒ `ready is False`; QA records не входят в production inventory.
- [ ] `uv run --frozen pytest -q tests/test_recover_cli.py tests/test_functional_gate.py` ⇒ FAIL.
- [ ] Соединить K/R interfaces; PowerShell wrapper получает verified tool archive/lock, разрешает `local-profile:cortel` и запускает CLI без API среды. Проверить release/config identity, login, saved projects, preview, save/generation/cancel/restore, MAX и integration identities в isolated QA clone; разрешение paid canary обязательно в QAScope. Временные QA effects никогда не используют рабочий webhook. До C-плана возможен только `DRILL_SNAPSHOT_VERIFIED`, не production `RECOVERY_READY`.
- [ ] Pytest + lint/types ⇒ PASS; cold drill с отключёнными исходными 3 VPS/registry, второй ПК, restart каждой роли и interrupt/retry всех фаз. Зафиксировать фактическое время и каждый preserved sentinel; без реального MAX/generation proof функциональную готовность не выдавать.
- [ ] Commit: `feat: expose verified one-command recovery workflow`.

## Зависимости, не скрываемые за mocks

R1 требует реального provider discovery/trust/fencing контракта, R2 — external CAS,
R3 — текущих effective configs, R5 — разрешённых QA целей/identities.
K4 использует собственный проверенный capture barrier; R4 physical replay добавляется в C1.
Можно разработать и проверить независимые части заранее, но эти границы
не заменяются fake adapters при выдаче live verdict.
