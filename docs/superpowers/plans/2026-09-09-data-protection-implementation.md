# Защищённые данные Omnia — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Делегирование — по применимым AGENTS.md; один владелец каждого набора файлов и каждой production-операции.

**Goal:** каждое поддерживаемое приложение получает обязательную защиту хранения, передачи, доступа и восстановления без технических настроек владельца.

**Architecture:** отдельная PostgreSQL каждого проекта сохраняется. Controller-owned профиль managed_data_v1 управляет encrypted storage, секретами, trusted Data API, DB policy и publication evidence. Рабочие данные отделены от среды генерации.

**Tech Stack:** текущие Python/FastAPI/asyncpg/Docker и Node22/MAX core; PostgreSQL установленной major-версии; проверяемое block/provider encryption; один сервис ключей; pgBackRest для DB backup/PITR.

**Spec:** [00-Архитектура-и-порядок-внедрения.md](../specs/2026-09-09-data-protection-design.md).

## Global Constraints

- Исходный HEAD исследования: 303ef6c1ef213d4e42518b82210ffdd4b3b23948.
- Рабочая ветка: codex/project-cell-cloud-20260902; upstream origin/main. Повторно проверить в начале исполнения.
- Исследование от 9 сентября учитывало restoration WIP. При переносе 10 сентября эти изменения уже вошли в f2e0177de992c8759a14ceafd0cc6683b436c227; перед исполнением обновить baseline и сохранить любые новые несвязанные изменения.
- Профиль production: managed_data_v1. Нет policy/ключа/доказательства mount — нет незащищённого fallback.
- Продукт не получает production DB credentials, KMS/backup/migrator/signing credentials.
- Не совмещать этот проект с major upgrade PostgreSQL или K3s rollout.
- Новые security-интеграции сначала работают на отдельной лаборатории.
- Тесты, предполагающие Docker/KMS/реальную БД, не могут считаться пройденными при skip или mock backend.
- Реализация включает все поддерживаемые пути публикации; MAX Project Cell — первая волна, не автоматическое доказательство остальных.
- Сроки RPO/RTO/retention из spec — инженерные цели до фактического измерения.
- Все production-изменения проходят существующий полный delivery loop.

## A. Организация исполнения

Каждая задача: конкретный красный сценарий → минимальная реализация → тест на реальном компоненте → diff review → проверки смежных путей → отдельный commit → push → документированная поставка → health/evidence. Inactive код допускается выпускать заранее, но не объявлять защиту включённой.

В начале каждого этапа записать checkpoint: цель, HEAD, текущий WIP, принятые интерфейсы, завершённые проверки, состояние push/deploy, оставшиеся блокеры и следующий безопасный шаг.

Не выполнять команды из этого документа против production без идентифицированной цели. Все приведённые pytest-команды запускаются из указанного каталога и относятся к будущей реализации; сейчас они не были выполнены.

Новые модули ниже — точные предлагаемые пути. При конфликте с параллельной работой сначала согласовать замену и обновить этот план, а не создавать второй механизм.

В сокращённых списках файлов services/ означает apps/orchestrator/src/omnia_orchestrator/services/, templates/ — apps/orchestrator/templates/, scripts/public-max-core/ — apps/orchestrator/scripts/public-max-core/, tests/ — apps/orchestrator/tests/. Явно указанные apps/api и apps/web относятся к своим приложениям.

## B. Зависимости

~~~mermaid
flowchart TD
    T01[01 Inventory и лаборатория] --> T02[02 Ключи]
    T01 --> T03[03 Хранилище]
    T02 --> T03
    T02 --> T04[04 TLS и credentials]
    T03 --> T04
    T04 --> T05[05 DB policy]
    T05 --> T06[06 Trusted Data API]
    T06 --> T07[07 Файлы и данные вне DB]
    T06 --> T08[08 Генератор и миграции]
    T02 --> T09[09 Backup]
    T03 --> T09
    T07 --> T10[10 Recovery]
    T09 --> T10
    T08 --> T11[11 Publication gate]
    T10 --> T11
    T11 --> T12[12 Статус и наблюдение]
    T12 --> T13[13 Legacy migration]
    T13 --> T14[14 Общий выпуск]
~~~

## C. Общие структуры и контракты

Первичная реализация типов — services/data_protection.py. Вынести сериализуемые API-схемы в schemas/data_protection.py, если это предотвращает импорт service-кода из router. Схемы versioned; незнакомая версия не трактуется как безопасная.

~~~python
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict

class ProtectionScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope_kind: Literal["project"] = "project"
    project_id: UUID
    workspace_id: UUID
    environment: Literal["draft", "candidate", "production", "recovery"]

class PlatformScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope_kind: Literal["platform"] = "platform"
    component: Literal[
        "control_db", "legacy_business_db", "control_state",
        "access_ledger", "object_store", "key_service"
    ]
    resource_id: str
    environment: Literal["production", "recovery", "test"]

EncryptionScope = ProtectionScope | PlatformScope

class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    check_id: str
    status: Literal["pass", "fail", "unknown"]
    checked_at: datetime
    evidence_digest: str
    reason_code: str | None = None

class ProtectionReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    report_version: Literal[1]
    scope: ProtectionScope
    policy_version: Literal["managed_data_v1"]
    release_digest: str
    core_digest: str
    schema_digest: str
    policy_digest: str
    storage_identity: str
    database_system_identifier: str
    fencing_epoch: int
    checked_at: datetime
    expires_at: datetime
    checks: tuple[CheckResult, ...]
    signature_key_id: str
    signature: str

class KeyRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key_id: str
    version: str
    purpose: Literal["credential", "backup", "artifact", "field", "evidence"]

class WrappedSecret(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: EncryptionScope
    key: KeyRef
    ciphertext: str

class RecoveryGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    grant_id: UUID
    operation_id: UUID
    source_scope: EncryptionScope
    target_scope: EncryptionScope
    backup_id: str
    permitted_keys: tuple[KeyRef, ...]
    expected_target_epoch: int
    expires_at: datetime
    issuer: str
    signature_key_id: str
    signature: str
~~~

Ограничения, которые нужно реализовать валидаторами: timezone-aware даты; expiry позже checked_at; неотрицательный epoch; корректные digest/identity форматы; уникальные check_id; отсутствие пустых checks; допустимый набор обязательных проверок. Signature вычисляется по канонически сериализованной структуре без поля signature и проверяется доверенным verifier. Один SHA-256 без защищённого источника не является подписью.

Результат проверки не содержит password, token, DSN, private endpoint, персональную строку DB или полный SQL error.

## T01. Inventory, базовый контракт и лаборатория

**Владелец:** основной инженер платформы. **Зависимости:** нет.

**Файлы:**

- Создать: apps/orchestrator/scripts/inventory_data_protection.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/data_protection.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/schemas/data_protection.py.
- Создать: apps/orchestrator/tests/security_lab.py.
- Создать: apps/orchestrator/tests/test_data_protection_inventory.py.
- Изменить: apps/orchestrator/tests/conftest.py.
- Создать: docs/operations/data-protection-inventory.md и закрытый deployment inventory вне Git.

**Вход:** текущие controller state/resources, Docker mount metadata, release identity и safe host/provider evidence.

**Выход:** полный список проектов/сред/кластеров/носителей/backup coverage; security-lock.json; структуры из раздела C; одноразовая лаборатория.

- [ ] Снять git status/HEAD/upstream; сопоставить WIP и уже поставленную версию. Не выполнять автоматический pull поверх изменений.
- [ ] Реализовать read-only inventory CLI с обязательными --output и --redact. Выводит только разрешённые metadata; не использует dump всего docker inspect/env.
- [ ] Сопоставить platform DB, legacy shared DB, draft/public/candidate/recovery, uploads, Redis, rootfs/checkpoints, controller state и все пути publication.
- [ ] Создать network_edges inventory: source/destination identity, data class, TLS/mTLS/локальный IPC, certificate verifier, responsible component и negative probe. Не пропустить HTTP gateway→product, API→orchestrator, Redis/MinIO, backup/KMS и telemetry.
- [ ] Для каждого носителя записать coverage: verified / unverified / not_applicable с причиной. Unknown нельзя преобразовывать в verified.
- [ ] Фактические PG/image/driver/OS/crypto версии записать в security-lock.json. Подтвердить свободную ёмкость, место/условия сервиса ключей и off-host storage.
- [ ] Создать отдельный Docker test project с UUID и labels. Два бизнеса, по два клиента, владелец и сотрудник; синтетические имена и canary values. Production endpoints запрещены в fixture.
- [ ] Добавить лабораторные fault hooks только в тестовый runner, без production HTTP endpoint для их включения.
- [ ] Реализовать безопасный cleanup: сравнить project label и полный allowlist созданных ресурсов; неизвестный ресурс не удалять.

Структура лабораторного helper: protected_lab.scope, protected_lab.owner, protected_lab.client_a, protected_lab.client_b, protected_lab.staff — authenticated HTTP-клиенты; protected_lab.product_exec(argv) — выполнение в продуктовой лабораторной среде; protected_lab.db_admin() и protected_lab.db_runtime() — отдельные async context managers подключения только к лабораторной БД; protected_lab.restart() — перезапуск её runtime.

Recovery-тесты используют отдельный recovered_lab, а не переключают protected_lab в production identity. HTTP-клиенты не подделывают пользовательские headers: получают настоящую синтетическую сессию через тестовый identity issuer доверенного gateway.

Начальный тест:

~~~python
def test_inventory_does_not_mark_unknown_storage_as_encrypted():
    from omnia_orchestrator.services.data_protection import classify_storage
    result = classify_storage(
        mount_found=True, provider_encryption=None, crypt_device=None
    )
    assert result.status == "unknown"
    assert result.reason_code == "storage_encryption_unverified"
~~~

Контракт classify_storage(*, mount_found: bool, provider_encryption: bool | None, crypt_device: str | None) -> CheckResult: отсутствие mount — fail; доказанный crypt mapping либо подтверждённый provider encryption — pass; отсутствие доказательства — unknown. Сбор доказательства отдельно; Docker label не является аргументом проверки.

**Проверка:** uv run --frozen pytest -q tests/test_data_protection_inventory.py из apps/orchestrator.

**Готово:** нет пропущенных поддерживаемых способов хранения/публикации; лаборатория не имеет production credentials.

## T02. Управляемые ключи и секреты

**Владелец:** инженер платформы/инфраструктуры. **Зависимости:** T01.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/key_provider.py.
- Создать: apps/orchestrator/tests/test_key_provider.py.
- Изменить: apps/orchestrator/src/omnia_orchestrator/services/cell_state.py.
- Изменить: apps/orchestrator/src/omnia_orchestrator/core/config.py.
- Создать: docs/operations/key-management-and-recovery.md.

**Интерфейс:** KeyProvider.wrap(scope: EncryptionScope, purpose, plaintext: bytes) -> WrappedSecret; unwrap(scope: EncryptionScope, value: WrappedSecret) -> bytes; rewrap(scope: EncryptionScope, value: WrappedSecret) -> WrappedSecret. Provider проверяет совпадение scope/purpose и собственную workload identity. Это протокол, а не самодельный шифр.

Для recovery отдельно: unwrap_for_recovery(grant: RecoveryGrant, value: WrappedSecret) -> bytes. Проверить подпись, issuer, срок, operation id, source scope/ciphertext/key match, target scope/epoch и принадлежность ключа указанному backup. Новая recovery-среда не обязана совпадать с source environment. Право даёт только этот краткоживущий grant; ordinary unwrap другого environment по-прежнему запрещён. Допускается resume той же операции, но не применение grant к другому target/backup. Новые target credentials шифруются под его новым scope.

- [ ] Записать выбранный backend, pinned version, место хранения, способ восстановления и оператора. До этого выполнять только лабораторный адаптер.
- [ ] Создать независимые policy identities для controller, backup, recovery и evidence signer. Продукт/генератор не входят в разрешённые identities.
- [ ] Перевести новые DB credentials на encrypted record; атомарное create с прежними O_EXCL/O_NOFOLLOW/0600/fsync свойствами.
- [ ] Реализовать versioned reader для старого plaintext record исключительно для контролируемой миграции. Не перезаписывать все secrets фоновым best-effort циклом.
- [ ] Убрать plaintext из exceptions, argv, structured logs, Docker Config.Env и snapshots. В разрешённом trusted процессе — tmpfs file/stdin или короткоживущий процессный secret.
- [ ] Реализовать request timeout, bounded retry с jitter, обработку revoked/denied/unavailable. Ошибка не включает режим без шифрования.
- [ ] Добавить rotation journal с key_id/version/operation_id и resume; не уничтожать старую версию до проверки удерживаемых копий.
- [ ] Репетиция восстановления самого key service и отдельного emergency access.
- [ ] Отдельные grant tests: production backup → authorized recovery target проходит; forged/expired/wrong source/target/key/operation/epoch отказываются. Runtime identity не может ни выпустить, ни использовать RecoveryGrant.

Красные проверки: чужой project/environment/purpose; отсутствие права decrypt; недоступность KMS; restart между записью ciphertext и commit metadata; утраченная старая версия при попытке restore.

Пример теста без настоящего секрета:

~~~python
async def test_wrong_project_cannot_unwrap(key_provider, project_a, project_b):
    import pytest
    from omnia_orchestrator.services.key_provider import KeyAccessDenied
    wrapped = await key_provider.wrap(project_a, "credential", b"synthetic-only")
    with pytest.raises(KeyAccessDenied):
        await key_provider.unwrap(project_b, wrapped)
~~~

Fixtures project_a/project_b — ProtectionScope лаборатории T01. key_provider обращается к отдельному реальному тестовому backend для release gate; unit fake используется только для проверки retry/error mapping.

**Проверка:** uv run --frozen pytest -q tests/test_key_provider.py tests/test_cell_state.py.

**Готово:** новый secret не обнаруживается в сохраняемых файлах/логах/артефактах как plaintext, старые данные читаются только разрешённой migration identity; cold recovery ключей пройден.

## T03. Зашифрованные носители без обходов

**Владелец:** инженер инфраструктуры. **Зависимости:** T01/T02.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/storage_protection.py.
- Создать: apps/orchestrator/scripts/verify_encrypted_storage.py.
- Создать: infra/systemd/omnia-protected-storage-check.service.
- Изменить: services/docker_machine_backend.py, services/docker_cell_resources.py, services/machine_environment.py, services/cell_checkpoint.py внутри orchestrator.
- Создать: apps/orchestrator/tests/test_storage_protection.py.
- Создать: docs/operations/encrypted-storage-migration.md.

**Интерфейс:** verify_storage(scope: ProtectionScope, resolved_paths: tuple[str, ...]) -> tuple[CheckResult, ...]. Проверяет все writable пути через trusted inventory, а не данные из продукта.

- [ ] Развернуть выбранный зашифрованный носитель в лаборатории. Для LUKS — существующий cryptsetup; не создавать собственную криптографию.
- [ ] Защитить полный список путей из spec, включая PostgreSQL temp/WAL, Docker layers, exports, logs и swap.
- [ ] Для новой infrastructure записать mapping node/device/filesystem/Docker mount и boot prerequisites.
- [ ] Добавить предварительную проверку до volume creation, start, restore и checkpoint write.
- [ ] При missing mount возвращать storage_mount_missing; при mismatch device — storage_identity_changed. Не создавать fallback directory.
- [ ] Проверить host reboot с доступным/недоступным key service; никакой пустой replacement DB.
- [ ] Проверить raw offline лабораторный носитель без unlock. Отсутствие canary через strings — только дополнительная проверка, основное evidence — криптографический mapping и невозможность mount/read без ключа.
- [ ] Подготовить миграцию действующих носителей через replacement/candidate; не переносить Docker data-root работающего узла командой mv.

Тест границы:

~~~python
async def test_missing_encrypted_mount_blocks_start(storage_lab):
    await storage_lab.unmount_expected_device()
    result = await storage_lab.start_new_project()
    assert result.reason_code == "storage_mount_missing"
    assert not await storage_lab.fallback_directory_created()
    assert not await storage_lab.postgres_started()
~~~

storage_lab — отдельная VM/узел тестовой инфраструктуры, не host production. Методы вызывают реальные mount/service проверки и возвращают metadata.

**Проверка:** test_storage_protection.py + restart/cold-unlock сценарий на disposable Linux VM.

**Готово:** каждый writable путь защищён либо документированно не содержит/не сохраняет чувствительные данные; coverage report не имеет unknown.

## T04. TLS, доверенные конфигурации и выдача credentials

**Владелец:** инженер платформы. **Зависимости:** T02/T03.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/database_transport.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/transport_protection.py.
- Создать: apps/orchestrator/tests/test_database_transport.py.
- Создать: apps/orchestrator/tests/test_internal_data_transport.py.
- Изменить: services/docker_machine_backend.py, services/machine_adapter.py, services/published_machine_backend.py.
- Изменить: templates/max-miniapp-nextjs/src/lib/db/index.ts в части доверенного core.
- Добавить тест: apps/orchestrator/scripts/public-max-core/database-tls.test.cjs.

**Интерфейс:** build_trusted_database_config(scope, role, certificate_ref) возвращает конфигурацию только trusted container; product_environment(scope) не содержит production DB secrets. Certificate issuer/key backend берётся из T02, scope — из T01.

- [ ] Выпускать leaf certificate для DB identity; хранить private key только у DB, CA trust — у доверенного клиента.
- [ ] Конфигурации postgresql.conf/pg_hba.conf принадлежат контроллеру и монтируются read-only. Продукт не управляет authentication.
- [ ] Разрешить нужные hostssl подключения, запретить plaintext/неизвестные identities; проверить порядок HBA.
- [ ] Для Node pg задать ssl объект с CA и rejectUnauthorized:true без конфликтующих ssl-параметров connectionString.
- [ ] Для asyncpg использовать проверяющий SSLContext; подтвердить hostname/SAN проверкой неверного имени.
- [ ] Убрать live DB credentials из build/install/start генерируемого продукта, включая старые переменные и Docker metadata.
- [ ] При rotation drain/recreate pool, terminate старые DB sessions; проверить старый пароль и уже открытое соединение.
- [ ] После restore/seed/reconcile применить trusted config повторно до открытия маршрута.
- [ ] Закрыть все network_edges из T01: trusted reverse proxy/взаимный TLS или уже поддерживаемый TLS клиента с проверкой peer. Новые remote HTTP/Redis/MinIO/KMS/object-store connections без верификации запрещены.
- [ ] Для loopback downstream выбрать TLS либо Unix socket с проверяемыми правами. Нельзя пометить обычный HTTP «защищённым», потому что он внутренний.
- [ ] Реализовать per-edge wrong-CA/wrong-peer/plaintext tests. Итоговый gate сверяет полный список рёбер с inventory; пропуск ребра даёт unknown/fail.

Фрагмент trusted Node config:

~~~typescript
const pool = new Pool({
  host: config.host,
  port: config.port,
  database: config.database,
  user: config.user,
  password: secretFile.read(),
  ssl: { ca: certificateAuthority, rejectUnauthorized: true },
  max: config.poolLimit,
});
~~~

config, secretFile и certificateAuthority загружаются доверенным loader из проверенной runtime-конфигурации; генерируемый продукт не является этим loader. Не копировать этот фрагмент вместе с secret в frontend.

**Проверка:** test_database_transport.py; test_internal_data_transport.py; Node database-tls.test.cjs; real pg_stat_ssl; wrong CA/name/expired cert/ssl disabled дают отказ. Internal transport suite отдельно проверяет gateway/core/product, control plane, Redis/MinIO, backup/KMS и разрешённые локальные IPC.

**Готово:** разрешённое соединение зашифровано и аутентифицировано; обход plaintext и imported trust HBA закрыт.

## T05. Общая DB policy вместо необязательного restoration-файла

**Владелец:** инженер DB/runtime. **Зависимости:** T04.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/database_policy.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/data_contract.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/access_ledger.py.
- Изменить после интеграции WIP: services/restoration_database.py, services/restoration_data_contract.py.
- Изменить: services/docker_machine_backend.py, services/cell_publication.py.
- Создать: apps/orchestrator/tests/test_database_policy.py.
- Создать: apps/orchestrator/tests/test_access_ledger.py.
- Расширить: tests/test_restoration_database.py и tests/test_restoration_machine_policy.py.

**Интерфейсы:** compile_data_contract(document, scope) -> CompiledDataContract; install_database_policy(scope, contract, operation_id, expected_epoch) -> PolicyInstallResult; inspect_database_policy(scope) -> tuple[CheckResult, ...].

CompiledDataContract содержит version, scope, entities, permissions, schema_digest, policy_digest. PolicyInstallResult содержит operation_id, epoch, installed_policy_digest и checks. Не хранит SQL password.

AccessLedger.append(scope, operation_id, expected_sequence, event) -> AccessLedgerHead; current_head(scope) -> AccessLedgerHead; project_current_memberships(scope, required_head) -> ProjectionResult. Head содержит sequence, digest и security_epoch; event содержит grant/revoke/ownership change и opaque subject. Журнал зашифрован, подписан, append-only, хранится вне откатываемой business DB. Событие подтверждается только после durable off-host append и условного обновления monotonic head под lease. Membership table проекта — производная проекция. Протокол и storage должны тестировать retry, race и невозможность восстановления более старого head поверх актуального.

- [ ] Выделить общую установку policy; restoration вызывает её, а не держит отдельную реализацию безопасности.
- [ ] Для protected production удалить fallback missing policy → postgres. Отсутствие документа означает блокировку запуска.
- [ ] Создать NOLOGIN owner, ограниченную omnia_runtime у Data API, отдельный migrator и backup role. Состав backup прав определить по фактическому инструменту, не давать их runtime.
- [ ] Включить FORCE RLS для всех private tables, USING/WITH CHECK, column grants и default deny.
- [ ] Снять опасные PUBLIC grants/memberships, CREATE/DDL/TRUNCATE/unsafe functions; проверить sequences, views, triggers, FOREIGN TABLE и SECURITY DEFINER.
- [ ] Сохранить проверяемую подпись actor claims; расширить binding на environment/audience и role revision. Простой user_id GUC запрещён.
- [ ] Политика владеет назначением owner и изменением роли; клиентское тело не меняет owner_id/tenant_id/business_role.
- [ ] Каталог старой БД с неизвестными privileged objects останавливает установку; не считать revoke на известных таблицах достаточным.
- [ ] Обеспечить идемпотентность, expected_epoch и recovery после частичного применения.
- [ ] Отзыв роли/capability меняет security_epoch и подтверждается после долговечного ledger event; новые requests проверяют актуальную проекцию. Начальная верхняя граница revoke latency — 60 секунд, без бессрочного кеша.
- [ ] При восстановлении business DB применить текущий ledger head, а не её исторические memberships. Недоказанная актуальность head блокирует открытие данных до независимого восстановления полномочий.

Проверка на реальной БД:

~~~python
async def test_runtime_cannot_become_database_admin(protected_lab):
    import asyncpg
    import pytest
    async with protected_lab.db_runtime() as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("SET ROLE postgres")
        attributes = await connection.fetchrow(
            "SELECT rolsuper, rolbypassrls, rolcreaterole "
            "FROM pg_roles WHERE rolname = current_user"
        )
        assert tuple(attributes.values()) == (False, False, False)
~~~

Дополнительно: чужой owner, UPDATE owner, INSERT wrong owner, прямой SQL без bridge, чтение guard secret, COPY PROGRAM, table owner bypass, подмена policy-файла и повторный restart.

**Проверка:** test_database_policy.py, test_access_ledger.py + существующие restoration DB tests в disposable PG.

**Готово:** реальный SQL-клиент не может обойти policy; plain root внутри продукта не превращается в DB admin.

## T06. Trusted Data API и понятный SDK

**Владелец:** инженер trusted core. **Зависимости:** T05.

**Файлы:**

- Создать: apps/orchestrator/templates/max-miniapp-nextjs/src/app/api/omnia/data/[...path]/route.ts.
- Создать: templates/max-miniapp-nextjs/src/lib/omnia/data/contract.ts.
- Создать: templates/max-miniapp-nextjs/src/lib/omnia/data/actor.ts.
- Создать: templates/max-miniapp-nextjs/src/lib/omnia/data/repository.ts.
- Создать: templates/max-miniapp-nextjs/src/lib/omnia/data/handler.ts.
- Создать: templates/max-miniapp-nextjs/src/lib/omnia/data-client.ts.
- Изменить: services/machine_boundary.py, services/machine_adapter.py.
- Изменить: scripts/public-max-core/prepare.mjs и сборку immutable core.
- Создать: apps/orchestrator/tests/test_trusted_data_api.py.
- Создать: templates/max-miniapp-nextjs/tests/data-client.test.mjs.

**API v1:**

~~~text
GET    /api/omnia/data/v1/entities/{entity}
POST   /api/omnia/data/v1/entities/{entity}
GET    /api/omnia/data/v1/entities/{entity}/{id}
PATCH  /api/omnia/data/v1/entities/{entity}/{id}
DELETE /api/omnia/data/v1/entities/{entity}/{id}
POST   /api/omnia/data/v1/transactions
~~~

Entity берётся из compiled contract. GET принимает allowlisted filters/sort и limit <=100. PATCH меняет только перечисленные поля; требует record version. CREATE и transaction требуют idempotency key. Transaction максимум 20 allowlisted операций без произвольного SQL; лимиты — начальный защищённый профиль.

Каждая mutation передаёт X-Omnia-Contract-Revision и X-Omnia-Writer-Epoch; CREATE/PATCH/DELETE/transaction требуют Idempotency-Key. PATCH и DELETE требуют If-Match с record version; каждый элемент transaction содержит expected_version для существующей записи. Разрешённый revision берётся из controller-owned compatibility registry активного release. Header лишь выбирает проверенную совместимость; не изменяет текущие права. Старый revision выполняется только через принятый адаптер с маской полей/правил удаления, иначе 409 contract_update_required. Неизвестный/отозванный writer epoch не допускается. Job capability также связывается с contract revision и writer epoch.

Owner/subject определяется gateway. Поддельные x-omnia-* удаляются. Отсутствие сессии — 401, доступ к чужому/несуществующему объекту — одинаковый 404, нарушение разрешённой операции — 403, превышение лимита — 413/429, conflict version/idempotency — 409.

- [ ] Реализовать отдельный project DB connection у immutable core; не смешивать managed core DB и dedicated business DB.
- [ ] Загрузить только подписанный controller-owned contract, проверить project/environment/digest.
- [ ] Реализовать permission matrix owner/staff/client/public/job; membership projection находится в trusted schema, но обновляется только из authoritative AccessLedger T05.
- [ ] Каждый DB request: transaction → verify actor → local context → parameterized query → commit/rollback → release connection. Никаких pooled session-global user variables.
- [ ] Составить filters через whitelist идентификаторов; значения — параметры. Закрыть mass assignment и JSON unknown-field overwrite.
- [ ] Реализовать contract compatibility registry и preconditions DELETE/batch. Прямой HTTP клиент с прежним revision не может выполнить новые разрушительные действия или расширить permissions.
- [ ] Добавить CSRF/Origin проверку для cookie-auth mutations, current-role checks, rate/size/time limits.
- [ ] SDK показывает понятные ошибки и не содержит security decision. Прямой HTTP клиент проходит те же проверки.
- [ ] Reserved routes нельзя перехватить продуктовым Next.js endpoint после route normalization.
- [ ] Проверить 1000 перемежающихся запросов A/B с pool, rollback, timeout и reconnect.
- [ ] Проверить публичный каталог без PII, запись клиента и рабочий доступ сотрудника.

Вертикальный тест:

~~~python
async def test_booking_is_private_and_survives_restart(protected_lab):
    path = "/api/omnia/data/v1/entities/bookings"
    created = await protected_lab.client_a.post(
        path,
        json={"service": "consultation", "slot": "2026-10-01T10:00:00Z"},
        headers={
            "Idempotency-Key": "synthetic-booking-1",
            "X-Omnia-Contract-Revision": protected_lab.contract_revision,
            "X-Omnia-Writer-Epoch": str(protected_lab.writer_epoch),
        },
    )
    assert created.status_code == 201
    record_id = created.json()["record"]["id"]
    await protected_lab.restart()
    own = await protected_lab.client_a.get(f"{path}/{record_id}")
    other = await protected_lab.client_b.get(f"{path}/{record_id}")
    assert own.status_code == 200
    assert own.json()["record"]["service"] == "consultation"
    assert other.status_code == 404
~~~

Fixture contract определяет booking service/slot, owner из verified actor и client self scope; значения синтетические. protected_lab.contract_revision и writer_epoch — metadata действующего лабораторного release. При перезапуске HTTP clients обновляют соединение, identity остаётся проверенной.

**Проверка:** test_trusted_data_api.py, SDK tests, реальная browser-запись/reload/смена пользователя.

**Готово:** полный бизнес-сценарий работает без прямого DB доступа продукта; контракт одинаково исполняется для UI и прямого HTTP.

## T07. Файлы, кеши, логи и дополнительные секретные поля

**Владелец:** инженер trusted core/платформы. **Зависимости:** T06.

**Файлы:**

- Создать: templates/max-miniapp-nextjs/src/app/api/omnia/files/[...path]/route.ts.
- Создать: templates/max-miniapp-nextjs/src/lib/omnia/files.ts.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/data_redaction.py.
- Создать: apps/orchestrator/tests/test_private_files.py и tests/test_data_redaction.py.
- Изменить существующие logging/observability adapters только на фактически выявленных inventory путях.

**Контракт файлов:** create upload intent → validate/check → complete immutable object version → commit DB reference. Скачивание через проверенное разрешение; public projection явная.

- [ ] Все uploads private; доступ проверяет project/entity/record/user и текущие права.
- [ ] Ввести лимит размера, MIME sniffing и безопасное Content-Disposition; активный контент — отдельный недоверенный origin.
- [ ] Ограничить upload/download tokens назначением и сроком; истёкший/чужой token отвергается.
- [ ] Исключить приватные ответы из общих кешей; проверить CDN, Next.js, service worker, preview и browser back после logout.
- [ ] Редактировать SQL errors, DSN, token, request body и customer values перед логами/Sentry/agent history.
- [ ] В additional-field profile шифрование выполняет trusted Data API через T02. Plaintext не поступает в поисковый индекс или логи. Сначала exact-use case и измерение search constraints; не делать все поля ciphertext произвольным model-кодом.
- [ ] Для базового выпуска все поля уже защищены storage encryption. Поддержка особо чувствительных полей требует отдельной принятой классификации и не включается скрытно.

Проверка:

~~~python
async def test_private_attachment_is_not_shared(protected_lab, private_attachment):
    download_path = private_attachment["download_path"]
    assert (await protected_lab.client_a.get(download_path)).status_code == 200
    assert (await protected_lab.client_b.get(download_path)).status_code == 404
    assert "private, no-store" in (
        await protected_lab.client_a.get(download_path)
    ).headers["cache-control"]
~~~

private_attachment — синтетический файл клиента A, созданный через upload intent/complete API, а не прямым обходом авторизации fixture.

**Готово:** приватный файл защищён так же, как запись DB; canary-секрет не попадает в logs/telemetry/prompts.

## T08. Генератор, миграции и служебные действия

**Владелец:** инженер runtime. **Зависимости:** T06.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/data_migrations.py.
- Изменить: services/cell_publication.py, services/published_machine_backend.py, services/machine_adapter.py.
- Интегрировать с WIP: services/code_restoration_engine.py, services/code_restorations.py.
- Изменить: apps/api/src/omnia_api/services/max_data_evolution.py и services/agent_native.py в части безопасных результатов/контрактов.
- Создать: apps/orchestrator/tests/test_production_data_boundary.py и tests/test_data_migrations.py.

**Интерфейс:** prepare_migration(scope, contract_digest, expected_epoch) -> MigrationPlan; apply_migration(plan_id, operation_id, expected_epoch) -> MigrationResult.

MigrationPlan: immutable id, source/target contract digest, allowed operations, expected DB system id, bounded time/lock budget, compatibility verdict. MigrationResult: state, applied digest, epoch, checks. Модель не выдаёт себе право выполнять произвольный SQL в production.

- [ ] Удалить все live DB mounts/credentials/network routes из generation/install/build/test, включая старые resume/recovery пути.
- [ ] Production process: non-root, read-only rootfs, без DAC_OVERRIDE/SETUID/SYS_CHROOT и других лишних capabilities; writable только approved encrypted mounts/tmpfs. Потребность build в root не переносится на public runtime.
- [ ] Новую production DB создавать из trusted bootstrap; не переносить arbitrary HBA/roles/functions из draft physical seed.
- [ ] Черновик содержит synthetic fixtures. Диагностика live данных выполняется доверенным инструментом с редактированным результатом; генератор не получает raw SQL output или screenshots клиентов.
- [ ] Схему менять через декларативный MigrationPlan; неизвестные functions/extensions/DDL отправляются в инженерный разбор и блокируют автоматический выпуск.
- [ ] Деструктивные операции/backfills отделить; сохранить исходные значения, bounded batches, durable checkpoints и сравнение контрольных значений.
- [ ] Обычный rollback кода не восстанавливает старую DB. Проверить unknown JSON fields, cascade deletes и прежние writers.
- [ ] Webhooks и jobs получают минимальную capability и idempotency, не admin DB credentials. Повтор после сбоя не повторяет платёж/уведомление.
- [ ] В candidate/recovery запретить production integrations, webhook deliveries и реальные платежи.

Проверка:

~~~python
async def test_product_cannot_find_production_database_secret(protected_lab):
    result = await protected_lab.product_exec([
        "python3", "-c",
        "import os; print(int(any(k in os.environ for k in "
        "['DATABASE_URL','PGPASSWORD','OMNIA_MIGRATOR_SECRET'])))"
    ])
    assert result.stdout.strip() == "0"
~~~

Это только одна проверка; matrix также проверяет network, volumes, /proc и экспорт артефактов. Не выводить значения найденных secrets даже в failed test.

**Готово:** генератор может обновить приложение и схему без чтения live records; откат кода сохраняет новые рабочие данные.

## T09. Полный encrypted backup каждого проекта

**Владелец:** инженер backup/operations. **Зависимости:** T02/T03; форматы файлов согласовать с T07.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/project_backups.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/backup_manifest.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/platform_backups.py.
- Создать: apps/orchestrator/scripts/backup_project_data.py.
- Создать: apps/orchestrator/tests/test_project_backups.py.
- Изменить: infra/backup/backup-omnia.sh; .github/workflows/offhost-backup.yml.
- Изменить: services/machine_environment.py и services/cell_checkpoint.py для protected artifact backend.
- Создать: docs/operations/project-backup-and-retention.md.

**Интерфейс:** backup_project(scope: ProtectionScope, operation_id) -> BackupManifest; backup_platform(scope: PlatformScope, operation_id) -> BackupManifest; commit_backup(manifest) -> BackupRef; enumerate_recovery_points(scope: EncryptionScope) -> tuple[RecoveryPoint, ...].

BackupManifest содержит version, scope: EncryptionScope, cluster system id/major/timeline для DB, backup/WAL refs, release/core/schema/policy digests, file object versions, encrypted artifact refs, key refs, created_at, recovery_point_at, signature. Для platform component неприменимые DB-поля явно отсутствуют по discriminated schema, а не заполняются фиктивным project_id. BackupRef: backup_id, scope, committed_manifest_ref, manifest_digest. RecoveryPoint: scope, point_at, required_backup_ids, required_key_refs, file_retention_watermark. Внешние endpoints и plaintext secrets не включаются в публичный отчёт.

- [ ] Сначала закрыть legacy plaintext staging: umask 077 до записи, encrypted staging, fail cleanup, отсутствие raw env вне защищённого доступа. Не удалять единственную копию ради чистоты.
- [ ] Развернуть pinned pgBackRest в лаборатории и включить backup/WAL для каждой независимой DB identity.
- [ ] Изолировать repo namespace, IAM и cipher secret между project/environment. Одна строка stanza сама по себе не является security boundary.
- [ ] Установить контроль recovery window, не только количества полных копий. Для 35 суток сохранять нужную full-before-boundary и WAL.
- [ ] Добавить encrypted off-host storage, versioned manifest, checks/signature, atomic committed marker.
- [ ] Checkpoint/rootfs/source exports шифровать до внешней передачи; проверять каждый export endpoint и ручной support download.
- [ ] Связать DB point и версии файлов; manifests/state/credentials references покрывают холодный запуск без старого host filesystem.
- [ ] Отдельно охватить control DB, legacy shared cluster, access ledger, controller state и object-store catalog. Shared physical backup имеет platform/cluster identity и отдельный IAM; проектное извлечение из него только в изолированном recovery.
- [ ] File GC защищает любой момент PITR: версии, созданные/удалённые между full/diff, сохраняются не менее 35 суток после последней ссылки/удаления плюс boundary reserve и pins. Object lifecycle подчиняется этому watermark.
- [ ] Retention использует единый controller lock: сначала доказать новый recoverable point, затем удалять только истёкшие неприкреплённые цепочки.
- [ ] Настроить backlog/reserve metrics и отсутствие silent WAL drop. Проверить quota/network outage.
- [ ] Ротацию cipher passphrase реализовать через новый repository; old repo остаётся recoverable весь срок.

Реальный тест:

~~~python
async def test_external_backup_does_not_need_plaintext_siblings(backup_lab):
    backup = await backup_lab.create_and_upload()
    await backup_lab.remove_local_staging()
    evidence = await backup_lab.verify_external(backup.backup_id)
    assert evidence.signature_valid
    assert evidence.encrypted
    assert evidence.complete
    assert evidence.scope == backup_lab.scope
~~~

backup_lab использует test object storage и pgBackRest; remove_local_staging удаляет только свои лабораторные UUID ресурсы. Этот тест не заменяет T10 recovery.

**Проверка:** test_project_backups.py + apps/api/tests/test_offhost_backups.py после изменения legacy.

**Готово:** каждый компонент inventory имеет committed внешний recovery source; пропуск DB/files/state даёт fail.

## T10. Recovery из внешней копии и аварийные испытания

**Владелец:** QA/operations; независим от автора backup. **Зависимости:** T07/T09.

**Файлы:**

- Создать: apps/orchestrator/scripts/restore_project_data.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/project_recovery.py.
- Создать: apps/orchestrator/src/omnia_orchestrator/services/platform_recovery.py.
- Создать: apps/orchestrator/tests/test_project_recovery.py.
- Изменить: infra/backup/restore-test-omnia.sh — убрать проверку внутри production PostgreSQL.
- Создать: docs/operations/project-disaster-recovery.md.

**Интерфейс:** restore_project(backup_ref: BackupRef, target_scope: ProtectionScope, operation_id, grant: RecoveryGrant, mode="isolated") -> RecoveryResult; restore_platform(manifest_refs: tuple[BackupRef, ...], target_inventory, operation_id, grants: tuple[RecoveryGrant, ...]) -> PlatformRecoveryResult. По умолчанию target environment=recovery; identity и namespace новые. Переключение production не входит в эти методы. PlatformRecoveryResult содержит component results, authoritative access head, новый global epoch и список недоказанных зависимостей; incomplete результат не разрешает маршруты.

RecoveryResult содержит восстановленный point, object coverage, schema/policy status, начало/конец, verification checks и component versions. Никаких customer rows.

- [ ] Отказаться от зависимости на plaintext siblings; скачать и расшифровать только внешние committed artifacts.
- [ ] Проверить подпись, identity/версии/major compatibility до записи в target.
- [ ] Проверить source→target RecoveryGrant; не требовать равенства production и recovery scope, но отклонять незаявленный источник/target/ключ/операцию.
- [ ] Не импортировать старую небезопасную DB authentication как authority. Trusted config и минимальная policy применяются до открытия сети.
- [ ] Наложить актуальный AccessLedger на восстановленные memberships; сменить session/capability/security epoch. При неизвестном current head оставить доступ закрытым, не восстанавливать уволенного сотрудника из старой строки.
- [ ] Для PITR test/recovery copy отключить archive push в исходный repository; не загрязнять его новой timeline.
- [ ] Восстановить связи DB/file versions и выполнить фактический бизнес-сценарий.
- [ ] Проверить PITR на момент между созданием и удалением файла, если оба события были между full/diff backup и GC уже запускался.
- [ ] Включить проверки неправильного ключа, отсутствующей версии, повреждённого файла/WAL, подмены manifest и чужого проекта.
- [ ] Смоделировать потерю исходного host/disks, используя только off-host data и независимый recovery ключей.
- [ ] Cold platform recovery: root keys/identity → key service → control DB/state без публичного доступа → актуальный AccessLedger и новый global epoch → object store/catalog → project DB/files → policy/proofs → маршруты/jobs. Старые leases и jobs не оживают автоматически.
- [ ] Измерить RPO/RTO отдельно для одного проекта и ограниченного массового восстановления.
- [ ] Cleanup строго лабораторный; при неизвестной identity оставляет ресурс для оператора, не делает DROP DATABASE.
- [ ] Контроль отзыва: после backup отозвать сотрудника и job capability, затем восстановить backup. Ни старая, ни новая сессия бывшего сотрудника не получает восстановленное право.

Проверка:

~~~python
async def test_recovered_business_record_and_attachment(recovered_lab):
    response = await recovered_lab.client_a.get(
        "/api/omnia/data/v1/entities/bookings/00000000-0000-4000-8000-000000000111"
    )
    assert response.status_code == 200
    assert response.json()["record"]["service"] == "consultation"
    assert await recovered_lab.attachment_digest_matches()
    assert await recovered_lab.other_user_denied()
    assert await recovered_lab.can_create_and_reload_new_record()
~~~

UUID и service — заранее записанная синтетическая fixture, включённая в исходную DB и внешний backup. recovered_lab создаётся исключительно T10 pipeline.

**Готово:** внешний архив восстановлен на отдельной среде, бизнес-функция работает, прежний узел и локальные файлы не нужны.

## T11. Обязательный publication gate и защита от гонок

**Владелец:** инженер control plane. **Зависимости:** T08/T10.

**Файлы:**

- Изменить: apps/orchestrator/src/omnia_orchestrator/services/data_protection.py.
- Изменить: services/cell_publication.py, services/published_machine_backend.py.
- Изменить: apps/api/src/omnia_api/services/cell_publication.py.
- Изменить: apps/api/src/omnia_api/services/deploy_attestation.py.
- Создать: apps/orchestrator/tests/test_data_protection_publication.py.
- Расширить: apps/api/tests/test_cell_publication_evidence.py.

**Интерфейс:** evaluate_publication(scope, expected_release_digest, expected_epoch) -> ProtectionReport; enforce_publication(report, actual_target) -> None. Ошибка — типизированный code; нельзя интерпретировать missing result как pass.

- [ ] Собрать обязательные checks storage/keys/TLS/roles/RLS/API/files/generator/backup/recovery.
- [ ] Не сводить их к bool из model output; source — контроллер и подписанные реальные probes.
- [ ] Привязать report к target DB system id/storage/release/core/schema/policy/epoch.
- [ ] Fresh report: исходный срок валидности 15 минут; финальные identity/policy/role проверки непосредственно под lease перед switch.
- [ ] Source/schema/policy/core/key/role changes инвалидируют соответствующий proof. Старый release attestation не достаточен.
- [ ] Проверить ВСЕ routes: first publish, republish, rollback, restore, wake/reconcile, legacy deploy, external deploy.
- [ ] Operation journal и fencing исключают TOCTOU/двойное переключение. После потерянного ответа результат определяется по фактическому active release.
- [ ] Failed candidate не меняет маршрут живого приложения и не затирает live records.

Проверка:

~~~python
async def test_report_from_another_database_cannot_publish(publication_lab):
    report = await publication_lab.valid_report()
    await publication_lab.replace_target_with_fresh_database()
    result = await publication_lab.publish_with_report(report)
    assert result.reason_code == "protection_target_changed"
    assert await publication_lab.previous_release_is_active()
~~~

**Готово:** профиль нельзя обойти удалением policy, env-флагом, legacy endpoint или восстановлением старого образа.

## T12. Понятный статус, drift и операционные правила

**Владелец:** инженер API/UI + operations. **Зависимости:** T11.

**Файлы:**

- Создать: apps/api/src/omnia_api/routers/data_protection.py.
- Создать: apps/api/src/omnia_api/schemas/data_protection.py.
- Создать: apps/api/src/omnia_api/services/data_protection_status.py.
- Создать: apps/web/src/components/max/MaxDataProtectionStatus.tsx.
- Изменить: apps/web/src/components/max/MaxWorkspaceShell.tsx после согласования с restoration WIP.
- Создать: apps/api/tests/test_data_protection_status.py.
- Создать: apps/web/src/lib/__tests__/data-protection-status.test.tsx.
- Создать: docs/operations/data-protection-incidents.md.

**API:** GET /api/projects/{project_id}/data-protection, только владелец/разрешённый staff платформы. Ответ: state, checked_at, backup_recovery_point_at, restore_verified_at, public_message. Внутренние grants/keys/hostnames не отдавать.

Состояния: preparing, protected, attention, migration_required. Состояние protected вычисляется по актуальным required checks, не вручную.

- [ ] Показывать только понятные факты: защита включена, копия актуальна, требуется действие Omnia.
- [ ] Убрать security switches у предпринимателя; приглашение сотрудника и export permission остаются бизнес-действиями.
- [ ] Запустить reconciler раз в 5 минут; deduplicate alerts по project/check/incident.
- [ ] Stale backup блокирует опасные migration/delete, но не выключает автоматически обычное чтение/работу сайта.
- [ ] Обход доступа вызывает точечную блокировку route/credential и отзыв сессий; не уничтожение данных.
- [ ] Проверить отозванного сотрудника, stolen session, rotation, inaccessible KMS и disk space incident.
- [ ] Operator audit: actor, purpose, project, operation, timestamp, outcome; без payload records.
- [ ] Определить владельца обновлений зависимостей/образов и regression проверки после security patch.

Пример контракта безопасного ответа:

~~~json
{
  "state": "protected",
  "checked_at": "2026-09-09T12:00:00Z",
  "backup_recovery_point_at": "2026-09-09T11:58:00Z",
  "restore_verified_at": "2026-09-09T10:00:00Z",
  "public_message": "Защита данных включена"
}
~~~

Это синтетический пример формата, не статус текущей production.

**Проверка:** API tests, UI states на desktop/768/390 px, keyboard, перезагрузка, отсутствие зелёного статуса при unknown.

## T13. Перевод существующих приложений

**Владелец:** единый migration owner + независимый QA. **Зависимости:** T12.

**Файлы:**

- Создать: apps/orchestrator/src/omnia_orchestrator/services/data_protection_migration.py.
- Создать: apps/orchestrator/scripts/migrate_project_protection.py.
- Создать: apps/orchestrator/tests/test_data_protection_migration.py.
- Создать: docs/operations/existing-project-protection-rollout.md.

**CLI:** inventory → rehearse → prepare → cutover → verify. Каждый mutating шаг требует project/environment/operation_id/expected_epoch и существующий rehearsal report. По умолчанию dry-run; live apply требует точный идентификатор подготовленной операции.

T13 разрабатывает и испытывает этот алгоритм на лабораторных представителях всех групп. Все пункты cutover ниже пока исполняются в лаборатории. Применение к реальным проектам выполняется только волнами T14 после допуска canary, backup/recovery и наблюдения. Завершение T13 не означает массовую миграцию пользователей.

- [ ] Разбить inventory на совместимые Data API, прямой SQL, custom functions, legacy storage и внешние размещения.
- [ ] Для каждого проекта построить контракт и проверенный mapping владельцев/ролей. Не угадывать авторизацию по названию столбца.
- [ ] Адаптировать код на candidate без изменения live, выполнить T06/T10 бизнес-сценарии.
- [ ] Проверить старые sessions, HBA, role memberships, SECURITY DEFINER, extensions и credentials из snapshots.
- [ ] Перед cutover установить fence всех writers и сохранить финальный recovery point.
- [ ] Перенести данные в доверенную среду и проверить контрольные значения/связи; никакой произвольной копии системных ролей.
- [ ] Ротировать прежние credentials, закрыть старые сессии, проверить plaintext/старый-password отказ.
- [ ] Атомарно переключить route; проверить новую запись, staff/customer roles, uploads, background job idempotency.
- [ ] После первой новой записи rollback использует актуальные данные; возврат к старому volume без синхронизации запрещён.
- [ ] Удаление старых носителей/backup по отдельной retention процедуре; до подтверждения отмечать наследованный остаточный риск.

Fault test:

~~~python
async def test_lost_cutover_reply_does_not_repeat_data_migration(migration_lab):
    await migration_lab.inject_reply_loss_after_switch()
    operation_id = await migration_lab.start_cutover()
    result = await migration_lab.reconcile(operation_id)
    assert result.state == "completed"
    assert await migration_lab.switch_count(operation_id) == 1
    assert await migration_lab.business_rows_match()
    assert not await migration_lab.admin_fallback_enabled()
~~~

**Готово:** механизм и rollback испытаны на представителях каждой группы; inventory содержит готовность/блокеры для будущих волн. Ни одна массовая production-миграция не выполнена в обход T14. Owner приложения не выполняет настройку DB/шифрования.

## T14. Полная приёмка и production rollout

**Владелец:** release owner. **Зависимости:** T13 и вся matrix.

**Файлы:**

- Создать: apps/orchestrator/scripts/smoke_data_protection.py.
- Изменить: .github/workflows/ci.yml.
- Создать: docs/operations/data-protection-release-evidence.md.
- При необходимости изменить: apps/llm-gateway/deploy/full/docker-compose.yml и effective host orchestrator unit через документированный путь.

**CLI smoke:** --suite принимает storage/access/backup/recovery/publication/all; --inventory указывает подписанный лабораторный/операционный manifest; --output пишет redacted JSON. Exit 0 только при всех required PASS; skip/unknown даёт ненулевой код. Release gate запрещает production target для fault tests.

- [ ] Full regression на disposable dependencies: API, orchestrator, web, trusted core.
- [ ] Реальный Docker/PG/TLS/backup/recovery suite; independent review всего merge-relevant diff.
- [ ] Security review публичного браузерного потока: sessions, roles, file URLs, CORS/CSRF, cache, XSS/external resource egress. Не считать DB encryption полной проверкой приложения.
- [ ] Зафиксировать exact commit, image digests, locked tools, inventory и закрытые finding IDs.
- [ ] Commit только intended files; push текущей ветки в согласованный upstream без force.
- [ ] Развернуть по production compose full из /opt/omnia и host omnia-orchestrator.service; infra/dev и K3s scaffold не использовать.
- [ ] Preflight: актуальная независимая recovery copy, отсутствие активных generation/cell operations/leases в затрагиваемой области; backup и write gate по runbook.
- [ ] Кандидат: миграции совместимы с текущими writers; сначала API совместимой версии, затем workers и orchestrator по проверенному порядку этапа.
- [ ] Проверить фактические process/image/release identity, health, worker heartbeats, trusted core digest.
- [ ] Применять алгоритм T13: owner canary → ограниченная группа → 10% → 50% → 100%. Допуск очередной волны проверяется ДО её cutover; failing matrix, regression или невыполненный RPO останавливает расширение.
- [ ] Каждая ступень наблюдается минимум один полный backup/restore цикл соответствующего пилотного профиля; короткий HTTP 200 не заменяет этот цикл.
- [ ] На 100% сверить inventory denominator: все действующие поддерживаемые приложения, не только созданные после feature flag.
- [ ] Закрыть plaintext legacy artifact retention, либо явно оставить milestone незавершённым.
- [ ] Финальный отчёт: revision/push/deploy/health, CRUD/reload/cross-user, external recovery, coverage, измеренные RPO/RTO и оставшиеся ограничения.

Команды regression из корня:

~~~powershell
Push-Location apps/api
uv run --frozen pytest -q
uv run --frozen ruff check src tests
uv run --frozen mypy src
Pop-Location

Push-Location apps/orchestrator
uv run --frozen pytest -q
uv run --frozen ruff check src tests
uv run --frozen mypy src
Pop-Location

Push-Location apps/web
pnpm test
pnpm lint
pnpm typecheck
pnpm build
Pop-Location
~~~

Полные проверки могут выявить существующие проблемы вне задачи; фиксировать baseline/новые ошибки отдельно, не объявлять неизвестный результат зелёным. CI jobs с общими базами/кешами не параллелить без изоляции.

Production shell-команды генерируются из проверенного deployment inventory на момент исполнения. Здесь намеренно нет команды format/move/delete/restore для неизвестного диска или live DB.

## D. Итоговый реестр deliverables

| Артефакт | Ответственная задача | Проверка |
|---|---|---|
| Полная карта данных/версий/путей | T01 | Нет необъяснённых пропусков |
| Ключи и recovery service | T02 | Отказ чужому scope и успешное аварийное восстановление |
| Encrypted storage coverage | T03 | Mount/boot/offline evidence |
| Проверенный TLS и отсутствие live secrets у продукта | T04 | Wrong CA/name/password, inspect/product negative |
| Обязательные DB grants/RLS | T05 | Прямые SQL попытки обхода |
| Trusted Data API/SDK | T06 | Реальный бизнес-путь и cross-user denial |
| Файлы/кеши/логи | T07 | Чужой файл и canary leakage |
| Изоляция генератора/мигратора | T08 | Невозможность чтения live и сохранение данных при обновлении |
| Полный внешний backup | T09 | Encrypted committed objects и полный manifest |
| Проверенный recovery | T10 | Новый узел без исходных дисков |
| Publication enforcement | T11 | Нельзя обойти через старый маршрут/отчёт |
| Наблюдение и понятный статус | T12 | Truthful status и ограниченная реакция на сбой |
| Проверенный механизм и миграция всех существующих групп | T13/T14 | Репетиции, допуск каждой волны, построчный inventory coverage |
| Production evidence | T14 | Полная matrix, review, SHA, health, canary, RPO/RTO |
