# Portable Recovery Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать полный переносимый комплект, который проверяется без прежних трёх VPS.

**Architecture:** Отдельный пакет `infra/recovery` запускается на компьютере владельца и не импортирует API/оркестратор. Версионированные manifests связывают секретный профиль, проверенные образы, топологию и полную копию. Существующие backup helpers переиспользуются через строгие exporters; состояние приложения не переустраивается.

**Tech Stack:** Python 3.12, uv lock, Pydantic, cryptography, httpx, pytest; существующие Docker/OCI и OpenSSL CMS. Версии нового пакета закрепить из совместимых текущих locks, затем выпускать вместе с проверенным recovery tool.

**Spec:** [Согласованная схема](../specs/2026-10-13-portable-recovery-design.md). Читать также [общий порядок и ограничения](2026-10-13-portable-recovery.md).

## Global Constraints

- Один `cortel.yleum-recovery`; публичный указатель `local-profile:cortel`; `YLEUM_RECOVERY_HOME` без пользовательского Windows-пути в Git.
- Роли `core`, `runtime`, `commerce`; Linux x86_64; полный набор артефактов по digest.
- `partial`, `deferred`, неизвестные и пропущенные обязательные тома запрещают readiness.
- JWT-derived credentials и `SECRETS_ENCRYPTION_KEY` сохраняются; plaintext secrets не выходят в stdout/Git.
- Неполный комплект не получает статус готового; runtime/prod сейчас не меняются.

## Review Focus

1. Новый Windows-профиль или второй ПК: переносимый файл разблокируется независимо от DPAPI первого ПК — задача K2.
2. Авторитетные custom PVC/черновики без стандартного имени: входят в обязательный inventory — K4.
3. Старая копия и несовместимый свежий `main`: выбирается связанный release/schema, не latest — K1/K3.
4. Архив с traversal/symlink или повреждённым ciphertext: нет небезопасной распаковки — K5.
5. Пустая работающая среда и неполный экспорт: различаются валидный нулевой inventory и отсутствие proof — K4.

## Структура и интерфейсы

Создать `infra/recovery/pyproject.toml`, `uv.lock`, `src/yleum_recovery/`, `tests/`.
Общий `contracts.py` задаёт Pydantic models с `extra='forbid'`, `format_version=1`:
`Role`, `RecoveryProfile`, `ReleaseManifest`, `BackupManifest`, `RestorePoint`,
`Inventory`, `Component`, `SecretMaterial`, `Proof`, `Failure`. `Component` задаёт
logical ID, kind, required, owner/binding metadata и capture handler ID;
`Inventory` содержит все роли, components и signed capture identity.
`Proof` содержит `ok: bool`, `failures: tuple[Failure, ...]`;
`Failure` — `code`, `component_id`, безопасное сообщение без секретных значений.
`RecoveryProfile` содержит имя, provider account/group/role selectors,
storage/lease/ingress adapters и `SecretMaterial` с credential values, ключами
и полными секретными exports всех ролей, а не только ссылками на утраченные VPS.
`SecretMaterial` хранится/сериализуется только внутри ciphertext, repr/logs redacted;
публичный manifest содержит лишь export ID. Физические IP в profile отсутствуют.
`ReleaseManifest` содержит full SHA, schema/protocol versions, platform/architecture,
component image digests, trusted archive hashes и certification evidence ID.
`BackupManifest` содержит release ID, secret export ID, required component inventory,
hashes, completeness, UTC boundary и per-component watermarks/object versions.
`RestorePoint` связывает manifests и `db_format: logical-snapshot | physical-wal`;
pg_dump не считается basebackup для WAL replay. Logical mode явно сообщает ненулевое RPO.
Короткие Python module paths ниже относительны `infra/recovery/src/yleum_recovery/`,
`tests/` — `infra/recovery/tests/`; `remote/` и `templates/` — `infra/recovery/`.

### K1: Строгие форматы и проверка совместимости

**Files:** Create `contracts.py`, `manifest.py`, `tests/conftest.py`, `tests/test_contracts.py`, package/lock files указанной структуры.
**Interfaces:** `verify_manifests(release: ReleaseManifest, backup: BackupManifest) -> Proof`; `select_restore_point(points: tuple[RestorePoint, ...]) -> RestorePoint`.

- [ ] Написать tests `test_complete_three_roles`, `test_missing_component`, `test_new_main_not_selected`, `test_logical_dump_cannot_replay_wal`: `assert proof.ok is True`, missing ⇒ `False`, chosen SHA равен backup release; несовместимая пара ⇒ `FORMAT_MISMATCH`.
- [ ] Из корня запустить `uv run --project apps/api --frozen pytest -o addopts='' -q infra/recovery/tests/test_contracts.py`; ожидается FAIL с отсутствующим `yleum_recovery`. Test conftest добавляет только новый `src` в import path.
- [ ] Реализовать models и две функции; создать самостоятельный проект/lock, read-only CLI `yleum verify`; никакой интеграции с production.
- [ ] Из `infra/recovery`: `uv run --frozen pytest -q tests/test_contracts.py`, `uv run --frozen ruff check src tests`, `uv run --frozen mypy src`; ожидается PASS.
- [ ] Commit только этих файлов: `feat: define portable recovery manifest contracts`.

### K2: Один секретный файл и разблокирование на компьютерах владельца

**Files:** Create `profile.py`, `local_unlock.py`, `redaction.py`, `tests/test_profile.py`; Modify `cli.py` для `profile activate`/`profile verify`.
**Interfaces:** `seal_profile(profile: RecoveryProfile, key: bytes) -> bytes`; `open_profile(ciphertext: bytes, key: bytes) -> RecoveryProfile`; `resolve_profile(name: str, home: Path) -> Path`; `activate_local_key(name: str, key: bytes) -> None`; `load_local_key(name: str) -> bytes`.

- [ ] Написать tests round-trip: `assert open_profile(seal_profile(p, k), k) == p`; tamper/key/name mismatch ⇒ `PROFILE_INVALID`; логи не содержат sentinel secret; fake second-PC unlock возвращает тот же профиль.
- [ ] Запустить `uv run --frozen pytest -q tests/test_profile.py`; ожидается FAIL для отсутствующих функций.
- [ ] Реализовать AES-256-GCM envelope со свежим 12-byte nonce и authenticated format/profile ID. Случайный 32-byte recovery key хранится отдельно владельцем; ежедневное разблокирование использует DPAPI текущего пользователя. DPAPI защищает только локальную обёртку ключа, а не переносимый файл; `LOCAL_MACHINE` не использовать. Активировать второй ПК через защищённый ввод ключа, не argv/env/chat; ACL ограничить текущим пользователем, неизвестную OS закрыть отказом. См. [AESGCM](https://cryptography.io/en/50.0.0/hazmat/primitives/aead/) и [DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).
- [ ] Запустить указанный pytest и lint/type checks; на двух доверенных Windows-профилях проверить activation/decryption синтетического файла. Ожидается PASS без plaintext следов.
- [ ] Commit: `feat: add portable encrypted recovery profile`.

### K3: Полный проверенный выпуск вне прежнего registry

**Files:** Create `release_bundle.py`, `storage.py` с protocol и release-asset adapter, `tests/test_release_bundle.py`, `.github/workflows/recovery-release.yml`; Modify `.github/workflows/ci.yml` только для recovery checks.
**Interfaces:** `build_release_bundle(source_sha: str, inputs: ReleaseInputs) -> ReleaseManifest`; `verify_release_bundle(manifest: ReleaseManifest, store: BlobStore) -> Proof`. `ReleaseInputs` определён в `release_bundle.py`, содержит immutable build inputs/locks/kit references; `BlobStore` protocol в `storage.py`: `put_verified(key: str, source: Path, sha256: str) -> str`, `fetch_verified(key: str, destination: Path, sha256: str) -> Path`.

- [ ] Tests `test_all_images_required`, `test_old_registry_offline`, `test_digest_arch_mismatch`, `test_ci_without_runtime_certification`: полный external bundle ⇒ `proof.ok`; missing image/digest/arch ⇒ `False`; CI alone ⇒ `certified is False`.
- [ ] `uv run --frozen pytest -q tests/test_release_bundle.py` ⇒ FAIL до реализации.
- [ ] Реализовать publisher на независимом build runner после CI точного full SHA. Сохранить API/web/gateway/billing, orchestrator source+Python/uv locks+units, machine/guard/core/helper/DB/Redis и входные kit/Node artifacts; OCI archives обязательны для offline registry recovery. Staging upload → verify → isolated runtime test → certified index. Archive/tag update атомарен, старые complete releases не перезаписываются; recovery VPS ничего не пересобирают.
- [ ] Новый pytest ⇒ PASS; regressions из `apps/orchestrator`: `uv run --frozen pytest -q tests/test_public_core_build_provenance.py tests/test_public_core_runtime.py`; пройти скачивание при заблокированном старом registry, сверить digests.
- [ ] Commit: `feat: publish independent certified recovery releases`.

### K4: Полный inventory и экспорт всех трёх ролей

**Files:** Create `inventory.py`, `capture.py`, `exporters.py`, `tests/test_inventory.py`; Modify `infra/backup/backup-omnia.sh`, `apps/orchestrator/scripts/backup_cells.py` для отдельного strict recovery mode; Create `infra/recovery/remote/export-role.py`.
**Interfaces:** `collect_inventory(probes: tuple[RoleProbe, ...]) -> Inventory`; `quiesce_capture(inventory: Inventory) -> CaptureFenceProof`; `export_complete(inventory: Inventory, fence: CaptureFenceProof, destination: Path) -> BackupManifest`. `RoleProbe` protocol в `inventory.py`: `role`, `enumerate_authoritative() -> tuple[Component, ...]`; каждый `Component` содержит logical ID, kind, required, owner/binding metadata и capture handler ID. `CaptureFenceProof` в `capture.py` содержит quiesced component IDs, epoch, cutoff, immutable capture handles и probes отсутствия писателей.

- [ ] Tests: zero-project signed inventory ⇒ complete; отсутствующий role probe ⇒ incomplete; custom PVC/private draft/checkpoint/environment artifacts ⇒ `required`; active/busy/deferred volume ⇒ `proof.ok is False`; writer freeze истёк через 300s без immutable handles ⇒ safe resume источника/incomplete.
- [ ] `uv run --frozen pytest -q tests/test_inventory.py` ⇒ FAIL.
- [ ] Реализовать reconciliation platform project list ↔ все host inventories. Экспортировать DB/globals, MinIO/исходники/versions, workspace/home/checkpoint/custom volumes/PVC, journals/queues/epochs, per-role effective env/config/systemd/nginx/certificates, app Secrets и UUID-bound credentials. Старые ciphertext/key pairs сохранить; private operational scripts переводить в templates только после redacted review. Snapshot mode: `quiesce_capture` закрывает доступ к записи и останавливает/drain все обнаруженные writer processes/pods/connections на исходной группе, оставляя только защищённый backup access; actual probes подтверждают quiescence до cutoff. Под freeze получить проверенные неизменяемые capture handles всех данных через поддерживаемый snapshot backend, затем безопасно открыть источник: долгий экспорт/шифрование/upload выполняется из handles вне перерыва. Если backend не умеет консистентный capture или прошло 300s, incomplete+safe resume, без бесконечного простоя ради полной копии. Неизвестный/неограждённый писатель ⇒ incomplete. C2 позже расширяет этот capture barrier контролем admission/epoch/ack. Непроверенные collectors не пропускать с `|| true`.
- [ ] Новый pytest ⇒ PASS; regressions: из `apps/orchestrator` `uv run --frozen pytest -q tests/test_backup_cells.py tests/test_cell_checkpoint.py tests/test_machine_environment.py tests/test_project_database_credentials.py`; из `apps/api` `uv run --frozen pytest -q tests/test_backup_bundle_verification.py tests/test_backup_project_sources.py tests/test_integration_credentials.py`.
- [ ] Commit: `feat: export complete three-role recovery inventory`.

### K5: Внешняя копия, расшифровка и квитанция полного комплекта

**Files:** Modify `storage.py`; Create `bundle_verify.py`, `tests/test_bundle_verify.py`; Modify `.github/workflows/offhost-backup.yml`; Create `docs/ops/portable-recovery.md`.
**Interfaces:** `verify_downloaded_bundle(point: RestorePoint, store: BlobStore, profile: RecoveryProfile, destination: Path) -> Proof`. Реальный storage adapter выбирается по доступному независимому хранилищу; контракт upload/download/checksum/retention проверяется до допуска. GitHub temporary artifact expiration не считается бессрочным хранением.

- [ ] Tests `test_ciphertext_only_on_second_pc`, `test_tar_escape_rejected`, `test_missing_secret_export`, `test_partial_not_promoted`: нет plaintext siblings; verified hashes сохраняются; unsafe member rejected до extraction; любой missing/partial ⇒ `False`.
- [ ] `uv run --frozen pytest -q tests/test_bundle_verify.py` ⇒ FAIL.
- [ ] Реализовать streaming CMS decrypt именно внешнего ciphertext с offline private key из профиля, безопасную распаковку и проверку всех manifests до import. Publish complete index только после decrypt/content proof; продлить retention/независимые копии по утверждённому storage контракту. В runbook описать одну activation, локальный указатель, проверку и отсутствие готового recover до R-плана.
- [ ] Pytest + lint/types ⇒ PASS; из `apps/api` `uv run --frozen pytest -q tests/test_offhost_backups.py`; два Windows-профиля подтверждают один downloaded bundle без старых VPS/registry. Секретный файл не добавлять в git.
- [ ] Commit: `feat: verify independent encrypted recovery kits`.

## Граница выпуска

После K1–K5 выдаётся только `KIT_VERIFIED`, не `RECOVERY_READY`. Реальный role export
требует восстановленного административного доступа и отдельного production scope.
Консистентный snapshot требует K4 capture proof для всех писателей; до его live
приёмки можно проверить полный синтетический комплект и read-only inventory,
но не объявлять production-копию согласованной.
