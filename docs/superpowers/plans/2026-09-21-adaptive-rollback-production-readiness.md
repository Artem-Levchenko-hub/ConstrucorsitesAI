# Адаптивные откаты: live acceptance, карта отказов и remediation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Зафиксировать фактическую живую приёмку всех пользовательских сценариев откатов, отделить PASS/FAIL/BLOCKED с точными идентификаторами от прошлых baseline и дать исполнимый remediation-план; проверить все три ветки без реальных бизнес-строк и автоматизировать блокировку небезопасного выпуска.

**Architecture:** Существующие `Restoration`, isolated candidate, proof, activation journal и reconciliation остаются единственным продуктовым механизмом. Отдельный закрытый QA runner создаёт одноразовые проекты с синтетическими данными и проверяет тот же пользовательский API, реальный runtime, SQL-свидетеля и durable receipts; он не открывает тестовые HTTP endpoints в production. Детерминированные сценарии и fault injection работают в выделенном Linux/Docker стенде, а узкая проверка настоящей адаптации — на одноразовом QA-проекте точного production release.

**Tech Stack:** Python 3.12, uv frozen environments, pytest/pytest-asyncio, PostgreSQL 16, Redis 7, FastAPI/SQLAlchemy/Alembic, Docker Project Cell, Next.js/React/TypeScript, pnpm 9.15.0, Vitest/jsdom, GitHub Actions. Новая очередь, новый restoration engine и отдельная система авторизации не нужны.

**Spec:** `docs/operations/2026-09-21-max-studio-rollback-live-result.md`, `docs/operations/2026-09-20-max-studio-rollback-production-readiness.md`, `docs/operations/2026-09-19-max-studio-versioning-fresh-e2e-audit.md`, `docs/operations/client-code-restoration.md`, `docs/operations/adaptive-versioning-baseline.md`, `docs/operations/versioning-v4-baseline.md`, `docs/plans/2026-09-21-max-rollback-completion.md`; уточнение владельца: прежние успешные live-сценарии сохраняются как baseline, повторяется изменённая защищённая цепочка на свежем release.

## Global Constraints

- База планирования: `0676e0be967b718b3c064c9ebbbb9c769fd36fdf`; `origin/main` совпал с HEAD после `git fetch --all --prune`, ahead/behind `0/0`, ветка аудита `codex/rollback-audit-20260921` была чистой.
- Перед каждой будущей задачей выполнить обязательный repository preflight, включая server comparison. Dirty/detached/no-upstream/diverged — остановка до безопасного разрешения; не stash/reset/rebase и не перезаписывать чужую работу.
- Production: документированное SSH-соединение `i48ptgvnis@170.168.72.200`, checkout `/opt/omnia`, только `apps/llm-gateway/deploy/full/docker-compose.yml`, Compose project `full`; host service `omnia-orchestrator.service` из `/opt/omnia/apps/orchestrator`. Источники этих значений: `docs/plans/2026-09-08-refactoring-handoff.md` и `docs/plans/2026-09-08-p01-postgres-probe-verification.md`.
- Нельзя брать production DB dump, бизнес-строки, сообщения, prompt, вложения, реальные пользовательские ключи или реальные signed launch data для QA. Разрешены schema-only catalog/fingerprint, явно синтетические fixtures, новые disposable DB/volumes и непрозрачные агрегированные счётчики.
- «Копия текущей схемы» означает DDL-only воспроизведение каталога в новой БД плюс synthetic seed. Это не копия реальной БД с последующим обезличиванием. Дамп синтетической QA-БД внутри штатного isolated-candidate механизма разрешён; источник обязан совпасть с QA manifest до открытия соединения.
- Пустая и совместимая ветки — один deterministic профиль `automatic`, без generation run, AI-вызова, quota consumption или wallet settlement. Несовместимая ветка не включает ИИ автоматически: только явный `adaptive` запуск и один idempotency key.
- Legacy `/rollback` восстанавливает Git/source и создаёт версию, а не возвращает бизнес-БД. `can_restore=false` для legacy MAX остаётся намеренным; пользовательский MAX-путь — `/restorations` и `MaxRestorationPanel`.
- `activation_effects_admitted=true`, потерянный ответ или недоступный controller не разрешают возврат старого кода. После возможных эффектов — наблюдение и forward recovery. Продуктовый schema downgrade и восстановление старых бизнес-строк запрещены.
- Данные, личности, ресурсы, receipts и release SHA связываются immutable manifest. Недостаточно зелёного UI, `completed`, совпадения HTTP-методов или общего `/health`.
- Все будущие изменения: проверка → commit → push `origin/main` без force → deploy pushed SHA через full Compose → service/HTTP/revision evidence. Архитектура/код восстановления — один Sol owner, Astra review; commit/push/delivery — Luna. Параллельные writers только в отдельных worktrees.
- Этот документ — результат аудита и планирования; product remediation этим не начата. После фиксации live-матрицы сам документ проходит обязательный delivery loop: проверка → commit → push origin/main → deploy pushed revision → service/HTTP/revision evidence. Docs-only характер не отменяет repository AGENTS.

## Review Focus

1. Ответ `activated` потерян после изменения runtime: повтор не должен запускать старые writers, повторную активацию или повторный расчёт — T08/T09.
2. Скрытое JSON-поле или денежная единица не участвуют в старом UI: успешный build/route parity не доказывает сохранение значения и смысла — T04/T11.
3. Пользователь B знает ID строки A и меняет `owner_id` в запросе: подписанная сессия B не должна читать, менять или удалять A — T05/T06.
4. Runner упал после создания ресурса, но до финального cleanup: повтор должен удалить только собственный terminal QA-проект и сохранить незавершённый activation journal — T03/T09/T14.
5. Native Windows/Node 25 дают локальный false red, а Linux E2E не запущен: environment limitation не превращается ни в application failure, ни в PASS — T12/T14.

---

## 0. Свежая live-матрица текущей задачи: промежуточный срез 21.09.2026, 18:05:28 МСК

Это фактический промежуточный отчёт для commit/push до 18:11 МСК. **Свежий автоматический empty exact restore прошёл E2E.** Приёмка всех сценариев ещё не завершена: compatible с данными, explicit adaptive и fault/restart/settlement matrix остаются следующими этапами. Задания T01–T19 ниже — remediation/automation plan, а не утверждение о внесённых исправлениях. Исторические PASS и current-SHA CI не подменяют результаты этой таблицы.

QA project: `361b3326-97b4-4c73-94d5-c8d277a69dc6`. Platform owner: `f5a028d8-eed4-4fe7-9452-5a056174fb39`; второй QA owner: `d18f1fa0-d0a8-478c-b2d2-e5e66889a939`. Использованы только новые синтетические QA-объекты. Evidence текущего среза — live UI и корреляция backend SQL/logs по указанным UUID; отдельные screenshot/evidence-файлы не сохранены. Не заявлять, что существует downloadable evidence bundle этого прогона.

| Case ID | Пользовательский сценарий | Фактический статус | Exact IDs / evidence | Что доказано / что сломалось |
|---|---|---|---|---|
| LIVE-01 | Начальная генерация v1 | PASS только terminal state; пользовательская работоспособность FAIL | run `e419642a-25e7-457d-aad7-264e9055bc63`, completed `2026-09-21T14:00:46Z`, 24m15s, workspace ready, fence=4 | Завершение примерно за 44s до 1500s deadline; build/tests/runtime_probe зелёные, но business endpoint не работает |
| LIVE-02 | Пустая исходная бизнес-область без seed | PASS | тот же run; `qa_clients_count=0`, пара QA-счётчиков `0/0`; seed INSERT отсутствует | Runtime запрет seed соблюдён; это не отменяет NEW-25 prompt defect |
| LIVE-03 / NEW-25 | Мастер сохраняет явный запрет demo data | FAIL | `apps/web/src/lib/max-brief.ts:114`, `buildMaxProjectPrompt`; тот же v1 run | Безусловный boilerplate требует «демонстрационные данные»; фактически seed не создан, нарушения данных не наблюдалось |
| LIVE-04 / NEW-26 | Открыть signed business endpoint v1 | FAIL / P0 | signed preview GET `/api/qa-clients` → 500, PostgreSQL `42P01`, relation `max_users` missing | `schema.ts` объявляет 9 MAX technical tables и QA tables; `drizzle/` содержит только `0001_qa_clients.sql`, starter `0000_max_core.sql`/`0001_business_core.sql` потеряны; green final proof не проверил этот маршрут |
| LIVE-05 | Repair v2 | FAIL, безопасный migration-contract отказ не доказал отсутствие DB effects | run `25e4f8a7-2398-461e-9de6-7a53ea4f417b`, failed через 14m11s, terminal `2026-09-21T14:21:13Z` | Unsafe absolute path был исправлен самим агентом; добавленные `0000`/`0001` нарушили append-only ordering; preview rollback сначала не удался |
| LIVE-06 / NEW-27 | После failed v2 вернуться к v1 без незаверсионированных эффектов | FAIL по DB/source атомарности; восстановленная доступность PASS | тот же failed v2, последующий restart v1; signed GET `/api/qa-clients` → 200, empty | Failed run оставил technical DB schema; исходная v1 стала работать после restart. Это **unversioned DB side effect**, а не доказательство чистого rollback или потери business rows |
| LIVE-07 | UI-only изменение → v3 | PASS | run `57602c09-efd6-4ff6-a6d4-9a0f19f881c9`, completed, 9m40s | Успешная пользовательская версия после failed v2; failed attempt расходует номер v2, следующая успешная — v3 |
| LIVE-08 | Replay запроса / параллельный prompt / reload active generation | PASS в проверенных случаях | run `57602c09-efd6-4ff6-a6d4-9a0f19f881c9`; idempotent replay вернул тот же run; другой prompt → 409 `generation_active` | Дублирующий run не создан; browser reload привязался к текущему run. Это не fault-matrix/settlement proof |
| LIVE-09 | Второй platform owner открывает чужой QA project | PASS | owners выше; owner2 получил 404 | Проверена граница platform project access. Signed business-row isolation A/B после восстановления пока BLOCKED, не считать этот 404 её заменой |
| LIVE-10 / NEW-28 | Автоматический empty restore v3 → v1, попытка 1 | FAIL восстановления / safe refusal PASS | restoration `8836a2ef-6487-4b9c-841e-8f9d043df417` | automatic policy; `database_state=empty`; `needs_changes` из-за unsupported historical schema; no candidate/selected branch/adaptation, `can_apply=false` |
| LIVE-11 | Cancel → F5/resume → reprepare empty restore | PASS управления; сам restore FAIL | restorations `e0de422b-bb90-400d-b0a3-cf384905c83c`, `798aabc3-bf86-488d-b0a5-75f6adddf2cc` | Те же empty/needs_changes/no-candidate исходы; manual cancel, F5 resume и повторная preparation работают |
| LIVE-12 | Prompt во время active restoration | PASS admission boundary | correlated active restoration из LIVE-10/11; ответ 409 `restoration_active` | Новый generation run не создан; запрещённая конкуренция не запущена |
| LIVE-13 | Control source v4 | PASS generation, не restoration acceptance | run `518ddd23-5582-4a1e-beb8-cc7be24cc3ac`, completed, 4m47s | Контрольный шаг для разбора исторической materialization; исторические v1–v4 не имели необходимого полного набора `scripts/apply-migrations.mjs` и `drizzle.config.ts` |
| LIVE-14 | Добавить allowlisted drizzle config → v5 | PASS точечного source diff / materializer command | run `f60076df-3230-4e9e-851f-2e08f9dea3c6`, completed, 5m03s | Diff только `drizzle.config.ts`; normalized SHA-256 `9822ef384a4b46621560fe7e96aa6e3880837b5c59dc0a9abfd5c2c4228b4d8a`; materializer command подтверждён |
| LIVE-15 | Successor v6, только UI/page | PASS generation/source scope | run `49a7c030-d768-4572-a8f7-1b775c5585d5`, completed, 7m47s | Diff только page; business DB counters остаются `0/0` |
| LIVE-16 | Supported empty restore v6 → v5 через UI | PASS E2E | restoration `4ecaa09a-c717-487d-be39-553062a537e5`, completed revision 7 в `2026-09-21T15:05:28Z`; candidate workspace `38e741e9-d257-5bac-96dc-b5fe98f76662` | automatic exact, без AI/adaptation; новая версия #7 `6c573766-013c-4554-9c20-a0d753ba0b35`, snapshot `4d1089a5-ef13-5dcd-b313-7f0cf4b88c04`; signed GET 200 count=0, DB clients/history `0/0` |
| LIVE-17 | F5 во время последней preparation | PASS | та же restoration `4ecaa09a-c717-487d-be39-553062a537e5` | UI после F5 вернулся в preparing, затем операция завершилась; ожидание build >3m не было failure |
| LIVE-18 | Compatible restore с synthetic rows, CRUD/hidden values/delete | BLOCKED на текущей точке продолжения, ещё не выполнено | QA project после успешной LIVE-16 остаётся empty | Следующий этап — явные synthetic rows/witness, затем compatible restore; empty PASS этого не доказывает |
| LIVE-19 | Incompatible explicit adaptive restore до completed | BLOCKED текущим этапом, не выполнено | В текущем QA project adaptation run не запускался | Прежний successful v8 от 19.09 — отдельный baseline; свежий protected path остаётся непроверенным |
| LIVE-20 | Post-activation signed two-owner, restart, adaptive receipt/settlement, 5 faults | BLOCKED, ещё не выполнено | Exact empty applied version/snapshot подтверждены LIVE-16; protected adaptive activation в текущем QA проекте отсутствует | Нельзя объявлять полную live matrix PASS; platform owner2 404 и empty signed read не заменяют hostile business-row/restart proof |

Дополнительные durable bindings LIVE-16: `database_strategy=replace_verified_empty`; source/candidate business inventory digests совпадают. Applied snapshot byte-for-byte эквивалентен target v5: **46 файлов**, списки added/removed/changed пусты. Applied application Git commit имеет подтверждённый префикс `6371b71b`; parent v6 snapshot — префикс `576b6c4e` (полные значения не получены в этом handoff, не дополнять их догадкой). Это SHA приложения, не platform release SHA. Перед завершением наблюдался `next build` с timeout 420s; terminal PASS supersedes прежний preparing/BLOCKED status, не отменяя предыдущие три unsupported-target safe refusals.

### 0.1. Stopping point и продолжение с другого компьютера

1. Открыть этот документ и QA project `361b3326-97b4-4c73-94d5-c8d277a69dc6` под owner `f5a028d8-eed4-4fe7-9452-5a056174fb39`; секреты/сессии не переносить через документ. Выполнить новый Git/server freshness preflight, записать actual release SHA.
2. **Первое действие — read-only сверить уже завершённую** restoration `4ecaa09a-c717-487d-be39-553062a537e5` через UI и GET `/api/projects/361b3326-97b4-4c73-94d5-c8d277a69dc6/restorations/4ecaa09a-c717-487d-be39-553062a537e5`. Ожидание — completed, current version #7, applied snapshot `4d1089a5-ef13-5dcd-b313-7f0cf4b88c04`. Не повторять apply и не создавать дубликат операции.
3. Сохранить полные application commit/parent snapshot, report/receipt и source/candidate inventory digests из этой operation; сверить actual signed GET 200 empty и business counts 0/0. Если наблюдение разошлось с terminal evidence, stop и reconcile; не отменять уже завершённую активацию.
4. Зафиксировать zero new generation/AI для exact ветки и отдельно проверить quota/wallet deltas, не подменяя settlement evidence фактом отсутствия adaptation. Выполнить свежие aggregate admission gates перед любым deployment restart; прежние нули до live-прогона не актуальная проверка.
5. **Следующее пользовательское действие** — создать только явные synthetic A/B записи, снять independent witness, выполнить compatible exact, затем несовместимое изменение и **explicit** adaptive. После каждого этапа новые operation/run/version IDs, hidden values, CRUD/reload/delete, signed owner boundary, restart и receipt settlement. Невыполненные пункты сохранять BLOCKED с причиной.
6. Зафиксировать именованные evidence artifacts следующего этапа: текущий срез основан на live UI/SQL/log correlation и отдельного архивированного screenshot bundle не имеет. Дописать результаты в новый commit этого документа; не переписывать прежние FAIL в PASS без связанного remediation run.

Docs-only commit/push данного промежуточного среза не означает product fix, полный live PASS или deploy. LIVE-16 terminal подтверждён; перед обязательным deployment всё равно повторить active admission gates. Push evidence и deployment evidence отражать отдельно, не считать завершение этой restoration разрешением перезапуска при другой активной работе.

## 1. База доказательств и границы свежести

### 1.1. Что уже проходило live

19.09 adaptive v8 завершился, сохранил синтетические A1–A3 и скрытые значения, позволил создать/обновить A4 и подтвердить reload; затем exact v9 сохранил четыре записи. Это положительный baseline, а не «адаптация никогда не работала». Независимые delete, вторая signed identity и fault matrix в том прогоне не доказаны. 20.09 empty и compatible ветки прошли live; их не нужно заново исследовать вручную — T10 превращает их в короткий автоматический release regression.

Новый isolated proof/activation path изменил защищённую цепочку. Последний несовместимый запуск 21.09: restoration `26a293c7-40c6-4699-8651-595458d05972`, generation `22f20a1e-372b-42c3-a779-2b117c1c2554`; terminal `failed/generation`, deadline `phase=edit`, `activation_effects_admitted=false`, applied version/snapshot отсутствуют, текущая v9 и строки сохранены. Причина этого конкретного отказа: общий 25-минутный edit/repair deadline и повторное исследование. Исправления `90b58990` (stage budgets) и `6c6357bd` (server-derived work plan) уже в production; успешного свежего завершения после них нет. Требуется узкий rerun защищённой цепочки, не повторный общий аудит.

### 1.2. Состояние на момент аудита

| Источник | Наблюдение | Что действительно доказано |
|---|---|---|
| Exact-SHA CI `35583452242` | API `2833 passed / 1 skipped / 2 xfailed`; orchestrator `2017 passed / 31 skipped / 15 xfailed`; isolated P01 `9 passed`; web `85 files`, typecheck/build PASS | Синтетические DB regressions и текущие проверки на `0676e0be`; не полный adaptive live acceptance |
| Локальный focused orchestrator | `637 passed / 34 skipped`; отдельно один Windows mode failure | Целевые rollback/versioning/publication regressions; POSIX mode на Windows не подтверждён |
| Локальный API/Web | API pure/unit `194 passed`; web `123 passed` при `NODE_OPTIONS=--no-experimental-webstorage` | Выбранные unit/UI contracts; первые 68 web failures — Node 25 harness, не 68 продуктовых дефектов |
| Local DB/Docker | PostgreSQL localhost отсутствует; Docker Desktop: отсутствует registry key `SOFTWARE\Docker Inc.\Docker Desktop`; real smoke требует Linux | Local DB-backed/migration/Docker E2E BLOCKED_ENV; exact-SHA CI даёт отдельное DB evidence, но не заменяет этот E2E |
| Production read-only | `/opt/omnia`, ветка `codex/production-p01-20260908`, HEAD/runtime web/API/worker/generation-worker/orchestrator `cb9cb1ea`, health OK | Все пять runtime identities согласованы на production revision |
| Delta local → production | 4 commits; только CI/docs/chart docs, restoration product source одинаков | Нет обнаруженного restoration source drift; release identities всё равно различаются |
| Server dirtiness | 5 modified SecondBrain-файлов, 123 untracked deploy/backup/env-history артефакта; application source не изменён | Требуется T14; запрещено очищать checkout по маске |
| Admission aggregates до live-прогона | unfinished generations, active Cell ops, leases, restorations, due reconciliation, publication activation/recovery — все 0 | Историческое тихое окно. LIVE-16 уже completed, но все aggregate gates после неё ещё нужно перечитать перед deploy/restart |
| Production smoke `35582571929` | `max_health.http_502`, `max_webhook.http_502`; live repro; последние 10 runs red | Canary недоступен, причина слоя UNKNOWN; smoke на `0676e0be` ещё не запускался |

Источник production/CI цифр — уже выполненный read-only аудит текущей задачи; они не выдаются за новые probes при написании этого файла. При исполнении сохранить его исходные санитизированные outputs в evidence bundle и обновить временные метки.

### 1.3. Наблюдаемая карта поломок

Application failure — воспроизведённое нарушение; environment limitation — запуск невозможен; unproven property — свойства ещё не доказаны на нужной цепочке. `UNKNOWN` не является названием причины.

| ID / класс | Слой | Симптом | Подтверждённая причина / UNKNOWN | Доказательство | Риск | Owner task | Exit criterion |
|---|---|---|---|---|---|---|---|
| A01 application | Public canary route | health и unauth webhook дают 502 | UNKNOWN: route/vhost/upstream/runtime ещё не разделены | smoke `35582571929`, live repro, 10 red runs | Внешняя работоспособность не подтверждена | T01 | Root-cause layer + regression; внешний 200/401 |
| A02 historical application | Adaptive generation | последний fresh run не дошёл до proof | Shared 25m deadline + повторное exploration; fixes уже deployed | operation/run выше; `90b58990`, `6c6357bd` | Исправление не прошло fresh terminal acceptance | T03 | Exact-release `completed` через protected activation, witness PASS |
| A03 historical fixed | Provenance/lock concurrency | ValidationError, ASGI lock traceback | Исправлены; после restart повторов не найдено | live-result/completion docs; production logs аудита | Отсутствие повтора без adaptive run не доказывает всю цепь | T03/T13 | Новая correlated trace без этих сигнатур; ожидаемый busy 503 |
| E01 environment | Windows filesystem | `0777` вместо POSIX `0700` | Native Windows mode semantics | `test_backup_cells::test_manifest_schema_and_private_file_modes` | Ложный CI/local вывод | T12 | POSIX assertion PASS Linux; portable assertions PASS Windows |
| E02 environment | Web harness | первые 68 web failures | Node 25 WebStorage collision | 123 PASS с отключённым experimental webstorage | Непереносимая локальная проверка | T12 | Supported Node baseline + явная Node 25 проверка |
| E03 environment | Local services | DB/migrations/Docker E2E не запущены | Нет PostgreSQL; broken Docker Desktop; Linux-only smoke | local preflight | Нельзя объявить local real E2E PASS | T12 | Exact-SHA Linux real runtime artifacts |
| U01 unproven | Protected adaptive chain | Нет fresh successful activation после fixes | UNKNOWN до T03 | последний run остановлен до proof | Главный release gap | T03–T08 | Все узкие checkpoints связаны одним manifest |
| U02 unproven | API/worker/controller recovery | Пять crash/lost-response точек не доведены до общего terminal witness | Unit coverage частично есть, integrated proof отсутствует | existing recovery tests, completion doc | Повторные effects/version/settlement | T09 | 5/5 deterministic fault runs, terminal invariants |
| U03 unproven | Data/auth/runtime | SQL hidden values, hostile B, delete, restart | Независимого связанного witness нет | audit 19/21.09 | Потеря скрытых данных или чужой доступ | T04–T07 | SQL + signed HTTP + restart evidence |
| U04 unproven | Semantics | JSON, units, functions/triggers, side-effects | Route parity и schema shape недостаточны | baseline docs | Ложная совместимость | T11 | Supported behavioral matrix; unsupported fail-closed |
| G01 automation gap | Release | Нет current-SHA smoke/adaptive canary gate | Smoke trigger ограничен paths; adaptive acceptance не mandatory | `.github/workflows/production-smoke.yml` | Устаревший зелёный результат принимается за новый | T02/T14 | Gate требует exact SHA и свежие artifacts |
| G02 automation gap | Operations | Нет полного phase/reconcile/activation/settlement dashboard | Есть backlog log, полного release dashboard нет | `restoration_reconciliation.py`, completion doc | Зависание видно поздно | T13 | Alerts + retry action с дедупликацией |
| G03 operations | Server checkout | Dirty non-main checkout | Сохранённая чужая работа и ops artifacts | read-only server audit | Непроверяемая поставка/ошибочная очистка | T14 | Сохранённая allowlist + чистый main deployment source |
| NEW-25 live application | Мастер исходного проекта | Добавлены «демонстрационные данные» при явном запрете | Безусловная строка `buildMaxProjectPrompt`, `apps/web/src/lib/max-brief.ts:114`; runtime seed фактически отсутствует | LIVE-02/03; v1 run | Конфликт пользовательского требования с boilerplate; данных в этом run не добавлено | T16 | Запрет сохранён в request; initial count 0 |
| NEW-26 live application P0 | Finalization + migration artifact | v1 completed/ready, signed business GET 500 `42P01 max_users` | Потеря starter migrations при наличии 9 technical declarations; green proof не вызвал signed business endpoint | LIVE-01/04, run `e419642a-25e7-457d-aad7-264e9055bc63` | Пользователь получает «готово» для неработающего приложения | T17/T18 | Fresh signed route + schema/migration inventory обязательны до completed |
| NEW-27 live application P0 | Generation failure / code–DB consistency | Failed v2 оставил technical DB schema; v1 restart стал отдавать 200 | Наблюдаемая unversioned DB mutation; точная граница migration side effect должна быть закреплена regression | LIVE-05/06, run `25e4f8a7-2398-461e-9de6-7a53ea4f417b` | Source rollback не отменяет DB mutation; current version скрывает schema change | T19 | До admission нет live schema changes; после effect только durable forward recovery |
| NEW-28 live application P0 | Empty historical materializer | empty → needs_changes, no branch/candidate/apply на v1 | Исторические v1–v4 не имеют полного required apply/config artifact набора; safe refusal confirmed | LIVE-10/11; три exact restoration IDs в §0 | Автоматический empty сценарий фактически не завершён | T18/T10 | Fresh supported v6→v5 terminal completed; missing artifacts дают точный safe blocker |
| NEW-29 resolved observation | Isolated candidate build | Supported v6→v5 был preparing >3m, затем completed | `next build` завершился; это не подтверждённый timeout defect | LIVE-16 completed `15:05:28Z`, applied #7, 46-file exact match, signed GET 200 | Длительность без terminal ранее ошибочно могла выглядеть как failure | T13 telemetry only | PASS: terminal/lineage/zero-data exact restore и F5 resume подтверждены |

## 2. Общие контракты исполнения

### 2.1. Пути и единицы ответственности

Существующие пути ниже проверены на базе плана. Пути с `Create` — намеренно новые. Не расширять большую `services/restorations.py` QA-кодом; отдельные модули runner размещать в `apps/api/src/omnia_api/ops/restoration_qa/`. Fixtures находятся в `apps/api/tests/fixtures/restoration_qa/`. Product changes допускаются только при красном test/probe, указывающем на соответствующий production module.

QA entrypoint: `python -m omnia_api.ops.restoration_qa.cli`. Подкоманды: `bootstrap`, `adaptive`, `witness`, `owners`, `crud`, `restart`, `receipts`, `faults`, `automatic`, `semantics`, `verify-bundle`, `cleanup`. `--manifest` обязателен для проверок уже созданного проекта; `--evidence-root` обязателен для bootstrap/automatic. Любой `--project-id` без manifest отвергается. `bootstrap` выводит в stdout только абсолютный путь созданного manifest; безопасные progress logs идут в stderr.

После реализации runner общие переменные устанавливаются в Linux из `apps/api` следующим образом. Изолированный режим используется для CI/faults; production-disposable выбирается отдельным явным `--mode production_disposable` только после проверки production identity и QA credentials:

```bash
export RELEASE_SHA="$(git rev-parse HEAD)"
export QA_ROOT="$(mktemp -d -t omnia-restoration-qa.XXXXXXXX)"
chmod 700 "$QA_ROOT"
export QA_MANIFEST="$(uv run --frozen python -m omnia_api.ops.restoration_qa.cli bootstrap \
  --profile incompatible --release-sha "$RELEASE_SHA" --mode isolated \
  --evidence-root "$QA_ROOT")"
test -s "$QA_MANIFEST"
```

```python
# Новый contracts.py; типы сериализуются Pydantic, extra='forbid', frozen=True.
class QaManifest(BaseModel):
    version: Literal[1]
    run_id: UUID
    release_sha: str                  # fullmatch [0-9a-f]{40}
    mode: Literal['isolated', 'production_disposable']
    project_id: UUID
    owner_id: UUID                    # platform QA owner, не MAX actor
    workspace_id: UUID
    fixture_digest: str               # fullmatch [0-9a-f]{64}
    database_identity_digest: str
    resource_manifest_digest: str
    created_at: datetime
    expires_at: datetime

class CheckResult(BaseModel):
    check_id: str
    status: Literal['PASS', 'FAIL', 'BLOCKED_ENV', 'NOT_RUN']
    observed_release_sha: str
    evidence_digest: str
    reason_code: str | None

class ReleaseIdentity(BaseModel):
    web: str
    api: str
    worker: str
    generation_worker: str
    orchestrator: str

async def assert_qa_scope(manifest: QaManifest) -> None: ...
async def read_release_identity() -> ReleaseIdentity: ...
async def bootstrap_fixture(profile: str, release_sha: str, mode: str) -> QaManifest: ...
async def run_adaptive(manifest: QaManifest) -> list[CheckResult]: ...
async def cleanup_owned(manifest: QaManifest) -> CheckResult: ...
```

Здесь сигнатуры задают создаваемые интерфейсы, многоточие означает Protocol declaration, а не недописанный шаг реализации. `assert_qa_scope` проверяет fresh runner journal, owner, project/workspace, fixture checksum, TTL и фактическую DB identity **до** чтения/мутации. Database URL, secret, cookie и launch data в manifest отсутствуют. Factory credentials передаются только process environment/private file descriptors, а client libraries используют `trust_env=False`.

### 2.2. Синтетическая модель

Fixture `qa_tasks` имеет `id uuid primary key`, `owner_id text not null`, `summary text not null`, `private_note text`, `payload jsonb not null`, `amount_minor bigint not null`, `created_at timestamptz not null`; отдельная `qa_task_events` хранит synthetic dependent history. Две signed MAX identity: A=`910000000001`, B=`910000000002`; это номера fixture, не реальные MAX accounts.

- v1: исторический UI «Название», API ожидает `title`, создаёт без `summary`.
- v2-current: UI «Описание», current schema использует `summary`; server-side адаптация v1 должна связать `title ↔ summary`, сохраняя остальные поля. Создание новой строки задаёт только согласованные безопасные значения; без произвольной миграции существующих строк.
- A1/A2 принадлежат A, B1 принадлежит B; stable UUID берутся из checked-in fixture. `payload={"visible":"alpha","hidden":{"tier":"qa-only","flags":[1,2]}}`, `private_note='synthetic-secret-A1'`, `amount_minor=12345`; фиктивные timestamps и адреса только `example.invalid`.
- CRUD создаёт A3, обновляет visible `summary`, проверяет reload, удаляет только A3 без dependent history. A1/A2/B1 и `qa_task_events` остаются invariants.
- Для exact compatible fixture v1/v2 различают только UI и nullable field, без schema incompatibility; empty fixture содержит ноль **бизнес-строк**, технические таблицы и MAX auth rows не подменяют presence.

Текущую production schema допускается читать только catalog SQL, без `SELECT` user relations и без extension function bodies, содержащих literals. Сохраняются object/type names, normalized constraints, digests; чувствительные defaults редактируются до экспорта. Основной воспроизводимый fixture не зависит от production catalog.

### 2.3. Общие команды проверки

Команды API выполняются из `apps/api`, orchestrator — из `apps/orchestrator`, web — из `apps/web`; Linux runner является authoritative для Docker/POSIX. БД создаются runner-ом, URL не переносится из production.

```bash
# API, на disposable PostgreSQL + Redis; uv.lock неизменен.
uv run --frozen pytest -o addopts='' -q
uv run --frozen ruff check .
uv run --frozen mypy src
# Orchestrator; RESTORATION_TEST_DATABASE_URL указывает restoration_policy_test.
uv run --frozen pytest -q
uv run --frozen mypy src
# Web; Node 20 CI baseline, pnpm 9.15.0.
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm build
# Repository root.
git diff --check
git diff --exit-code -- apps/web/pnpm-lock.yaml apps/api/uv.lock apps/orchestrator/uv.lock
```

Не выполнять полный suite после каждого маленького checkbox: targeted red/green внутри task, полный exact-commit CI перед delivery. Если task намеренно меняет lockfile, последнее утверждение заменяется review конкретного dependency diff; этот план не требует новых runtime dependencies.

### 2.4. Обязательный delivery contract D

Каждый task ниже включает D, даже если его результат — только tests/ops/docs. Самостоятельные read-only probes не требуют бессмысленного product commit.

1. Зафиксировать targeted red/green, негативные случаи, типизацию, data/diff sanity и exact-head CI. Reviewer проверяет evidence, не повторяет те же tests без причины.
2. Передать Luna точный allowlist файлов и указанный commit message. Повторный fetch, чистый integration checkout; push в `origin/main` без force. Если origin сдвинулся — безопасно интегрировать и повторить затронутые проверки на новом SHA.
3. Пока server dirty/non-main не разрешён T14, deploy BLOCKED. Нельзя «для теста» обойти preflight. После T14 использовать только full Compose и документированный host orchestrator, не `infra/` dev stack.
4. Перед recreate/restart: нулевые aggregate unfinished generations, active Cell operations/leases/restorations, due reconciliations, publication activation/recovery; сохранённые exact image IDs, schema compatibility и конфигурационные hashes. Не копировать business dump в QA/evidence.
5. Deploy **pushed** revision; API migration readiness предшествует workers; совместимые images API/worker/generation-worker, web SHA отдельно, host orchestrator restart только если требуется изменением. Не подставлять backend SHA вместо фактического web SHA.
6. Проверить `docker compose -p full -f apps/llm-gateway/deploy/full/docker-compose.yml ps`, `systemctl is-active omnia-orchestrator.service`, `/web-health`, `/api/health`, dependency heartbeats и exact identities; затем внешние canary 200/401, T02 smoke и task-specific QA check.
7. Сохранить commit/push/deploy IDs, image IDs, HTTP codes, время, CI/smoke run URLs, bundle digest. При ошибке task не `done`: отчёт называет конкретный незавершённый этап и следующая работа продолжает delivery, а не новый scope.

## 3. Зависимости и порядок

```text
T01 read-only 502 diagnosis ──→ T02 current-release smoke
             │                         ↑
             └── root-cause fix ─── T14 checkout/release gate

T12 portable Linux harness ─┐
T03 synthetic fixture/runner ├─→ T04 SQL witness ─→ T05 signed owners
                           │                         │
                           └─→ T08 receipt tests     ├─→ T06 CRUD ─→ T07 restart
                                                     │
T03 + T04 + T05 + T06 + T07 + T08 ─→ one fresh adaptive acceptance
T08 + harness ─→ T09 five faults ────────────────────┤
T03 + T04 + T05 ─→ T10 automatic profile ─────────────┤
T04 + T05 + T06 ─→ T11 semantics ────────────────────┤
T13 telemetry/retry + T02 + T14 ─────────────────────┴─→ release decision
T15 UX refinements ─→ affected regression + ordinary delivery

Current LIVE-16 observe → terminal/reconcile → admission gate
T17 signed business proof + T18 starter artifacts + T19 DB effect fencing
    → fresh generation/empty restore regression → protected adaptive acceptance
```

Приоритет P0 задаёт blocking importance; T12/T14 разрешено выполнить раньше зависимых P0, поскольку без среды и чистой поставки их live acceptance невозможен. Сначала T01 read-only diagnosis; никаких speculative product fixes. Участки независимых read/test work могут идти параллельно, максимум два; shared product files редактируются одним owner последовательно.

## 4. P0 work packages

### Task T01 — локализовать и исправить canary 502 доказанным минимальным изменением

**Files:**
- Read/Test: `apps/api/src/omnia_api/ops/production_smoke.py`, `apps/api/tests/test_production_smoke.py`, `.github/workflows/production-smoke.yml`.
- Read: `apps/orchestrator/src/omnia_orchestrator/services/nginx_writer.py`, `apps/orchestrator/src/omnia_orchestrator/services/cell_publication.py`, `apps/orchestrator/templates/max-miniapp-nextjs/src/app/api/max/webhook/route.ts`.
- Create: `apps/api/src/omnia_api/ops/restoration_canary_diagnosis.py`, `apps/api/tests/test_restoration_canary_diagnosis.py`, `docs/operations/2026-09-21-max-canary-502-diagnosis.md`.
- Conditional Modify/Test, только по найденной ветке: `apps/orchestrator/src/omnia_orchestrator/services/nginx_writer.py` + `apps/orchestrator/tests/test_nginx_writer.py`; либо `apps/orchestrator/src/omnia_orchestrator/services/cell_publication.py` + `apps/orchestrator/tests/test_cell_publication.py`; либо webhook route + `apps/api/tests/test_max_project_kit.py`; либо `.github/workflows/production-smoke.yml` + `apps/api/tests/test_production_smoke.py`.

**Interfaces:** Consumes existing `Configuration.from_env`, `run_smoke`, документированную serving route/controller identity; Produces `diagnose_canary(observations: list[dict[str, object]]) -> dict[str, object]` с `layer`, `reason_code`, `observed_codes`, `release_sha`, `evidence_digest`. `layer` — `external_route|nginx|runtime|auth|unknown`; не чинить, если `unknown`.

- [ ] 1 (2–5 мин): получить `PRODUCTION_MAX_CANARY_URL` через `gh variable get PRODUCTION_MAX_CANARY_URL`; проверить HTTPS и отсутствие userinfo/query. Сохранить hostname и route digest, не секретные URL. Отдельно сохранить текущие public release identities.
- [ ] 2 (2–5 мин): повторить только GET `/api/health` и POST `/api/max/webhook` без Authorization, body и user event; ожидаются 200 и 401. Текущий expected red — 502/502; не отправлять валидный webhook, который способен вызвать действие.
- [ ] 3 (2–5 мин): read-only SSH `git rev-parse HEAD`, `git status --short`, `docker ps`; определить container/route по controller/publication записи, а не угадывать имя. Из `nginx -T` извлечь только совпавший server block и upstream; полный secret-bearing конфиг в bundle не писать.
- [ ] 4 (2–5 мин): GET health через public DNS, локальный nginx с тем же `Host`, затем найденный upstream из его namespace. Сверить route target с durable publication release, container labels/image/mount identity; прочитать ограниченный интервал nginx upstream errors с редактированием payload.
- [ ] 5 (2–5 мин): выполнить unauth POST на каждом уже найденном hop; измерить только code и auth-denial shape. Если downstream healthy, а upstream hop 502 — причина между ними. Если runtime health не 200 — изучить его startup/port/readiness, не менять auth ожидание на 502.
- [ ] 6 (2–5 мин): зафиксировать classifier tests, запустить red:

```python
def test_runtime_ok_and_nginx_502_classifies_nginx():
    result = diagnose_canary([
        {'hop': 'external', 'health': 502, 'webhook': 502},
        {'hop': 'nginx', 'health': 502, 'webhook': 502},
        {'hop': 'runtime', 'health': 200, 'webhook': 401},
    ])
    assert result['layer'] == 'nginx'

def test_missing_runtime_observation_is_unknown():
    assert diagnose_canary([{'hop': 'external', 'health': 502}])['layer'] == 'unknown'
```

- [ ] 7 (2–5 мин): реализовать чистую классификацию; red до неё — import/name error. Добавить `test_auth_200_is_security_failure`, `test_foreign_route_binding_rejected`, `test_diagnostics_never_echo_env_or_body`; assertions: unexpected 200 не PASS, foreign binding stop, payload/token отсутствует в JSON.
- [ ] 8 (2–5 мин на изменяемую функцию): выбрать fix по доказанной ветке: stale URL → исправить QA canary registration и проверить принадлежность; неверный vhost/upstream → минимальный writer/binding fix; runtime crash/port → точечный startup fix; 200/403/500 вместо 401 → восстановить auth-before-effects. Если существующего canary больше нет, создать новый **synthetic** через обычную публикацию, не переназначать чужой проект.
- [ ] 9 (2–5 мин запуска): `uv run --frozen pytest -q tests/test_restoration_canary_diagnosis.py tests/test_production_smoke.py`; соответствующий conditional product test обязан воспроизводить точный неверный hop и стать green. Длительное ожидание test — не повод объединять другие изменения.
- [ ] 10: выполнить D. Commit `fix(ops): diagnose and repair the verified MAX canary failure`. Evidence: 200/401 на всех relevant hops, exact route/runtime binding, новый внешний smoke run; сохранить и первоначальные 502. Если fix только ops state, commit фиксирует проверенный runbook/regression, а изменение state записывается отдельно.

**Observability/negative cases:** unknown DNS, TLS error, wrong Host, отсутствующий container, healthy foreign container, untrusted body, auth 200. Сервис платформы healthy при canary 502 не переводит A01 в resolved.

### Task T02 — связать smoke с точной поставленной ревизией

**Files:** Modify `.github/workflows/production-smoke.yml`, `apps/api/src/omnia_api/ops/production_smoke.py`; Test `apps/api/tests/test_production_smoke.py`; Create `apps/api/tests/test_restoration_release_gate.py`.

**Interfaces:** Consumes `ReleaseIdentity`/D и T01 external 200/401; Produces smoke artifact `smoke.json` с `runner_sha`, expected/observed пятью SHA, `started_at`, `finished_at`, failures и `status`. Новый `validate_smoke_identity(runner_sha: str, expected: ReleaseIdentity, observed: ReleaseIdentity) -> list[str]` никогда не подменяет expected наблюдаемым.

- [ ] 1: написать `test_smoke_rejects_old_runner_for_current_release`, `test_smoke_rejects_independent_generation_worker_drift`, `test_missing_canary_cannot_skip_release_gate`. Assertions: mismatched SHA → nonzero, missing canary → nonzero, generation-worker сравнивается отдельно, даже при общей expected worker revision.
- [ ] 2: `uv run --frozen pytest -q tests/test_restoration_release_gate.py`; initial red — отсутствующий validator/artifact. Сохранить текущий `0676e0be` как audit base, не жёстко кодировать его в будущем gate.
- [ ] 3: добавить explicit workflow_dispatch release input и проверку checkout SHA; artifact write `if: always()`, incident policy сохранить. Любая новая failure возвращает неуспех в финальном шаге независимо от `continue-on-error` probe.
- [ ] 4: после D/T14 установить expected vars только из проверенной поставки, затем `gh workflow run production-smoke.yml --ref main`; получить run через `gh run list --workflow production-smoke.yml --limit 5 --json databaseId,headSha,status,conclusion`, выбрать точный `headSha`, `gh run view RUN_ID --json headSha,conclusion,jobs` (RUN_ID — ID выбранного run, не «последний вообще»).
- [ ] 5: запуск на старом deployed `cb9cb1ea` с expected `0676e0be` должен честно FAIL identity. Это допустимый диагностический результат до deploy, но не release PASS. После поставки всего release run обязан PASS и иметь точный SHA.
- [ ] 6: `uv run --frozen pytest -q tests/test_production_smoke.py tests/test_restoration_release_gate.py`; `actionlint .github/workflows/production-smoke.yml`; выполнить D с commit `ci: gate production smoke on exact release identity`.

**Negative/observability/evidence:** mismatch любого из пяти сервисов, stale vars, unknown SHA, пропавший canary, HTTP 502, incomplete artifact → FAIL. Bundle содержит workflow run URL и обе карты identities; ручной curl не заменяет exact-SHA workflow.

### Task T17 — NEW-26: terminal success требует signed business route proof

**Files:** Modify `apps/api/src/omnia_api/services/max_runtime_probe.py`, `apps/api/src/omnia_api/services/max_finalization.py`; Test `apps/api/tests/test_max_runtime_probe.py`, `apps/api/tests/test_max_finalization.py`, `apps/api/tests/test_max_finalization_integration.py`; Create `apps/api/tests/test_max_signed_business_proof.py`.

**Interfaces:** `probe_signed_business_endpoint(client: httpx.AsyncClient, path: str, expected_status: int = 200) -> dict[str, object]` возвращает только safe status/reason/shape digest. Consumes server-owned signed preview session и route contract из fixture; Produces обязательный proof item перед promotion. Generated self-test и anonymous platform health не заменяют этот пункт.

- [ ] 1: написать `test_missing_max_users_prevents_completed_despite_green_build`, воспроизводящий LIVE-04: current schema declares max_users, fresh disposable PostgreSQL имеет только QA migration; signed GET `/api/qa-clients` возвращает 500 `42P01`; assert finalization не `completed`, current version не promoted, reason=`signed_business_route_failed`.
- [ ] 2: `uv run --frozen pytest -q tests/test_max_signed_business_proof.py`; expected red на текущем green-final-proof пути. Добавить контроль: anonymous health 200 и invalid webhook 401 не делают signed business route успешным.
- [ ] 3: минимально включить signed read выбранного actual business route в existing finalization proof; route path выбирается accepted contract, не произвольным URL от model; timeout bounded, redirects/egress forbidden, response payload не попадает в logs/agent context.
- [ ] 4: `test_signed_empty_business_route_is_valid`, `test_business_route_500_does_not_settle`, `test_proof_refuses_foreign_preview_binding`: 200 empty считается корректным пустым приложением; 500/foreign binding блокирует promotion и terminal quota settlement; no fallback to unsigned probe.
- [ ] 5: green `uv run --frozen pytest -q tests/test_max_signed_business_proof.py tests/test_max_runtime_probe.py tests/test_max_finalization.py tests/test_max_finalization_integration.py`; свежий synthetic v1 reproduction показывает signed GET 200 до «готово», иначе precise safe failure.
- [ ] 6: выполнить D, commit `fix(max): require signed business endpoint proof before completion`. Evidence: new run ID, exact release, signed status, migration inventory и отсутствие прежнего 42P01; UI success только после этих проверок.

**Negative/observability:** missing table, missing owner auth table, expired signature, permission denied, wrong response shape, foreign app, stale preview. Status/error code можно публиковать; cookie/query/body нельзя. Задача P0 выполняется перед новым полноценным generation/rollback acceptance, а не маскируется увеличением deadline.

### Task T18 — NEW-26/NEW-28: starter migrations и historical materializer artifacts

**Files:** Modify `apps/api/src/omnia_api/services/max_project_kit.py`, `apps/api/src/omnia_api/services/max_data_evolution.py`, `apps/orchestrator/src/omnia_orchestrator/services/restoration_catalog.py`, `apps/orchestrator/src/omnia_orchestrator/services/restoration_empty.py` только в доказанном участке; Test `apps/api/tests/test_max_project_kit.py`, `apps/api/tests/test_max_data_evolution.py`, `apps/orchestrator/tests/test_restoration_catalog.py`, `apps/orchestrator/tests/test_restoration_empty_materializer.py`; Read authoritative starter files `apps/orchestrator/templates/max-miniapp-nextjs/drizzle/0000_max_core.sql`, `apps/orchestrator/templates/max-miniapp-nextjs/drizzle/0001_business_core.sql`, `apps/orchestrator/templates/max-miniapp-nextjs/scripts/apply-migrations.mjs`, `apps/orchestrator/templates/max-miniapp-nextjs/drizzle.config.ts`; Create `apps/api/tests/test_max_starter_artifact_preservation.py`.

**Interfaces:** `validate_starter_artifacts(files: Mapping[str, str], baseline: Mapping[str, str]) -> list[str]` даёт named errors `starter_migration_missing`, `migration_order_changed`, `materializer_script_missing`, `drizzle_config_missing`. Consumes immutable starter revision/hash; Produces pre-effect validation and actionable historical report. Старый target не объявляется compatible просто после подстановки современной схемы.

- [ ] 1: red `test_declared_max_tables_require_preserved_starter_migrations`: bundle с 9 technical table declarations и только `0001_qa_clients.sql` rejected до runtime mutation. `test_current_starter_keeps_apply_script_and_drizzle_config` проверяет точные checked-in files, а не строку в prompt.
- [ ] 2: `uv run --frozen pytest -q tests/test_max_starter_artifact_preservation.py tests/test_max_project_kit.py`; expected initial red — потерянный artifact не блокирует текущую readiness цепь. Mutation remove 0000/0001 либо config обязана оставаться red после исправления.
- [ ] 3: сохранить required starter migrations/config/script при materialization/merge; не разрешать model удалить их незаметно. Existing-history SQL append-only проверяется до исполнения; восстановление потерянной baseline migration оформляется approved forward repair с новым порядковым номером, не задним числом вставкой 0000/0001.
- [ ] 4: historical extraction требует точного набора artifacts либо named `needs_changes`; missing config/script не должен превращаться в неясное «unsupported schema». Verified-empty materializer может использовать только явно allowlisted deterministic config с hash binding и доказанной schema intent, не arbitrary current template injection.
- [ ] 5: regression `test_missing_historical_materializer_is_actionable_without_candidate`, `test_allowlisted_config_restores_empty_target_without_ai`, `test_nonempty_database_never_uses_empty_repair`, `test_unchanged_config_hash_binds_candidate`. Вторая проверка воспроизводит LIVE-14/16 с normalized hash `9822ef384a4b46621560fe7e96aa6e3880837b5c59dc0a9abfd5c2c4228b4d8a`; wrong hash rejected.
- [ ] 6: API green выше + `tests/test_max_data_evolution.py`; orchestrator `uv run --frozen pytest -q tests/test_restoration_catalog.py tests/test_restoration_empty_materializer.py`; fresh empty restore обязан пройти terminal receipt, а не только успешно запустить materializer command.
- [ ] 7: выполнить D, commit `fix(max): preserve starter migrations and bind historical materialization`. Evidence: before/after artifact manifest, exact source diff, synthetic zero business counters, terminal empty restore and no generation/settlement.

**Negative/observability:** missing versus corrupt artifact различаются; unknown inventory не empty; no candidate на unsupported target остаётся безопасным отказом, но не success. Прежние три failed empty attempts сохраняются в отчёте, даже если fresh supported path успешно завершится.

### Task T19 — NEW-27: failed generation не скрывает незаверсионированную DB mutation

**Files:** Modify `apps/api/src/omnia_api/services/max_finalization.py`, `apps/api/src/omnia_api/services/max_managed_generation.py`, `apps/api/src/omnia_api/services/max_data_evolution.py`, `apps/api/src/omnia_api/services/generation/agent_verification.py` только после reproduction; Test `apps/api/tests/test_max_finalization_integration.py`, `apps/api/tests/test_max_managed_generation.py`, `apps/api/tests/test_max_data_evolution.py`; Create `apps/api/tests/test_generation_schema_failure_atomicity.py`; Read `apps/orchestrator/src/omnia_orchestrator/services/restoration_adaptation_workspace.py`, `apps/orchestrator/tests/test_runtime_hot_reload_migrations.py`.

**Interfaces:** existing generation/adaptation operation сохраняет source DB identity/schema digest до первого effect. New test seam `capture_schema_effects(run_id: UUID) -> dict[str, object]` — только test/ops witness, не новый public API; output before/after schema digest, migration journal digest, serving source commit, effects-admission state. Existing protected adaptive workspace переиспользуется там, где он уже предусмотрен; обычную generation не объявлять изолированной без доказательства.

- [ ] 1: exact synthetic regression `test_failed_append_only_repair_does_not_mutate_live_schema`: source lacks max_users, repair пытается добавить исторические 0000/0001, contract fails; compare source catalog before/after. На текущем воспроизведении expected red — technical schema изменилась, хотя run failed и version не promoted.
- [ ] 2: `uv run --frozen pytest -q tests/test_generation_schema_failure_atomicity.py`; отдельно `test_source_restart_cannot_hide_failed_run_schema_effect` требует явного durable outcome даже если GET после restart стал 200. Business counts zero не отменяют schema-effect defect.
- [ ] 3: переставить deterministic migration contract validation до любого apply/build hook, который запускает migrations; rehearsal разрешить только isolated candidate DB. Сделать минимальную правку найденной границы, не вводить production downgrade для очистки следов.
- [ ] 4: если live effect уже admitted, записать bound durable schema/activation intent и довести forward; запрещено возвращать старый source с текстом «данные не изменены». При отсутствии достоверного evidence state остаётся reconciling и конкурирующая generation blocked.
- [ ] 5: тесты `test_contract_rejection_has_zero_source_ddl`, `test_lost_migration_response_is_indeterminate`, `test_failure_after_admission_keeps_forward_recovery`, `test_failed_preview_restore_reports_original_and_cleanup_failures`; assertions: zero pre-admission DDL, no old writers post-admission, original error не теряется за cleanup error, one active source/schema pair.
- [ ] 6: green `uv run --frozen pytest -q tests/test_generation_schema_failure_atomicity.py tests/test_max_finalization_integration.py tests/test_max_managed_generation.py tests/test_max_data_evolution.py`; real disposable PostgreSQL обязателен, mock-only catalog не закрывает NEW-27.
- [ ] 7: выполнить D, commit `fix(generation): fence schema effects before failed-run recovery`. Live rehearsal только на новом synthetic QA project; сохранить before/after schema witnesses, source version/run IDs и terminal durable outcome.

**Negative/observability:** запрещён old business dump, DDL rollback через удаление таблиц, ручной возврат state в ready и повтор paid run без анализа. NEW-27 не означает доказанную потерю business rows: наблюдались technical schema additions и разрыв связи с успешной version.

### Task T03 — одноразовый synthetic fixture и один fresh completed adaptive run

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/__init__.py`, `contracts.py`, `fixtures.py`, `runner.py`, `cli.py`, `evidence.py` внутри того же нового package; Create `apps/api/tests/test_restoration_qa_runner.py`, `apps/api/tests/fixtures/restoration_qa/manifest.json`, `schema.sql`, `seed.sql`, `v1.json`, `v2.json` в той же fixture directory. Read existing `apps/api/src/omnia_api/services/repo.py`, `project_versions.py`, `restoration_adaptation.py`, `generation_deadline.py`, `apps/api/src/omnia_api/routers/restorations.py`, `apps/api/src/omnia_api/routers/messages.py`. Test existing `apps/api/tests/test_generation_deadline.py`, `apps/api/tests/test_adaptation_work_plan.py`, `apps/api/tests/test_restoration_adaptation.py`.

**Interfaces:** Implements contracts §2.1. `fixtures.py` produces deterministic source bundles/hashes and owned resource journal; `runner.py` runs ordinary prepare → explicit prompt adaptation → poll/reconcile, returning CheckResults. `bootstrap_fixture` writes only fresh QA project history and synthetic DB, using existing `repo.init_from_files`/`repo.commit_files` and Snapshot/ProjectVersion models; it never fabricates restoration completion or activation receipts.

- [ ] 1: создать fixture manifests с stable row IDs, v1/v2 file digests, fixed migration DDL, forbidden output keys. Проверить, что SQL содержит только schema/rows этого fixture, network destinations — loopback/runner allowlist.
- [ ] 2: добавить `test_bootstrap_refuses_existing_foreign_project`, `test_runner_rejects_non_qa_database_before_connect`, `test_fixture_setup_creates_no_generation_runs`, `test_adaptive_requires_explicit_consent_profile`; expected red — package отсутствует. Перед сетевым вызовом fixture scope должен уже пройти проверку.
- [ ] 3: реализовать private bootstrap: новый platform QA owner/project через штатный admission, fixture Git snapshots и versions в одной согласованной transaction, runtime start штатным API; QA DB role только к собственному DB. Bootstrap не использует AI и не становится общедоступным HTTP endpoint.
- [ ] 4: unit assertions для happy path и duplicate idempotency:

```python
async def test_adaptive_retry_keeps_one_run_and_activation(qa_runner):
    manifest = await qa_runner.bootstrap('incompatible')
    first = await qa_runner.start_adaptive(manifest, key='qa-adapt-once-0001')
    second = await qa_runner.start_adaptive(manifest, key='qa-adapt-once-0001')
    assert first.run_id == second.run_id
    result = await qa_runner.wait_terminal(first.operation_id)
    assert result.state == 'completed'
    assert result.activation_effects_admitted is True
    assert result.applied_version_id is not None
    assert await qa_runner.count_adaptation_runs(manifest) == 1
```

`qa_runner` — новый fixture из `test_restoration_qa_runner.py`; он связывает реальные API services с scripted controller в unit layer. Это не доказательство настоящего live model run.

- [ ] 5: реализовать poll с monotonic deadline и durable resume marker. Для incompatibility `needs_changes` фиксируется **до** explicit adaptive prompt; automatic policy не запускает generation. Повтор после cancel использует сохранённый request/key; новый terminal failed не возобновляется под старым ID.
- [ ] 6: `uv run --frozen pytest -q tests/test_restoration_qa_runner.py tests/test_generation_deadline.py tests/test_adaptation_work_plan.py tests/test_restoration_adaptation.py`; expected PASS, отдельные DB tests выполняются на disposable PostgreSQL.
- [ ] 7: выполнить D, commit `test(qa): add isolated synthetic adaptive restoration runner`. Включение fresh production-disposable запуска ждёт T04–T08, T02 и T14; не расходовать реальный generation на недостроенный свидетель.
- [ ] 8: один явный `uv run --frozen python -m omnia_api.ops.restoration_qa.cli adaptive --manifest "$QA_MANIFEST"` после подготовки всех checkpoints. Временные бюджеты читать из runtime settings; текущий baseline edit 25m, repair 15m, sealed proof/activation 40m — потолки этапов, а не обещание длительности. Записать final state, stage times, restoration/run/activation IDs, exact code/runtime identity.
- [ ] 9: отсутствие `completed` — собрать конкретный stage/tool/blocker и минимальный reproduction, не запускать бесконечные оплачиваемые retries. Прежние `ValidationError`, `workspace_lock_timeout` traceback и `operation_id=unknown` проверять по allowlisted signature, без transcript/prompt dump.

**Exit/evidence:** completed protected chain + T04–T08 PASS на одном manifest; historical v8 остаётся отдельным baseline. Fail-before-activation обязан оставить current snapshot, database identity и witness неизменными. Cleanup проверяет собственные resources и сохраняет unresolved journals.

### Task T04 — независимый SQL witness, связанный с activation

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/witness.py`, `apps/api/tests/test_restoration_qa_witness.py`; Modify new `contracts.py`, `runner.py`, `evidence.py`; Read `apps/api/src/omnia_api/schemas/restoration.py`, `apps/orchestrator/src/omnia_orchestrator/services/restoration_binding.py`.

**Interfaces:** `capture_witness(manifest: QaManifest, checkpoint: str) -> Witness`; `assert_preserved(before: Witness, after: Witness, allowed_changes: dict[str, object]) -> CheckResult`. `Witness` содержит run/release/project/workspace/DB identity, fixture digest, checkpoint, schema digest, sorted row-ID set, HMAC каждого канонического значения, foreign/dependent row counters и capture timestamp. Raw значения остаются только в fixture; секрет HMAC случайный на run, private до verify и уничтожается после.

- [ ] 1: добавить failing `test_hidden_value_change_fails_even_when_row_count_matches`, `test_witness_rejects_same_rows_from_foreign_database`, `test_witness_detects_deleted_dependent_row`, `test_schema_fingerprint_does_not_require_business_rows`.
- [ ] 2: `uv run --frozen pytest -q tests/test_restoration_qa_witness.py`; expected red missing module. Mutation assertion:

```python
def test_hidden_json_change_is_detected(witness_factory):
    before = witness_factory(payload={'hidden': {'tier': 'qa-only'}})
    after = witness_factory(payload={'hidden': {'tier': 'lost'}})
    result = assert_preserved(before, after, allowed_changes={})
    assert result.status == 'FAIL'
    assert result.reason_code == 'hidden_value_changed'
```

- [ ] 3: реализовать отдельное read-only SQL-соединение с `BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY`; explicit projection всех fixture business columns и зависимых IDs. Сортировка UUID/ключей JSON, timestamps UTC, numeric strings, различение NULL/empty; без `SELECT *`, arbitrary SQL или app-generated report как источника истины.
- [ ] 4: checkpoints `before_prepare`, `after_candidate_proof`, `after_activation`, `after_crud`, `after_restart`, `after_fault_recovery`. Сравнить source DB identity на всех preserve-current этапах; кандидат обязан иметь иную DB identity и тот же synthetic baseline.
- [ ] 5: перед capture перепроверить manifest ownership/DB identity и allowlisted table names. Нет manifest, TTL истёк, источник не synthetic, schema изменена после proof, missing checkpoint — FAIL до data query.
- [ ] 6: добавить DB tests `test_repeatable_read_witness_is_consistent`, `test_null_unicode_numeric_timezone_canonicalization`; параметризовать hidden null, Unicode, `0`, `-1`, decimal и nested arrays. Запуск `uv run --frozen pytest -q tests/test_restoration_qa_witness.py` на disposable PostgreSQL.
- [ ] 7: выполнить D, commit `test(qa): bind independent SQL witnesses to restoration identity`; `uv run --frozen python -m omnia_api.ops.restoration_qa.cli witness --manifest "$QA_MANIFEST" --checkpoint before_prepare`, затем runner вызывает остальные checkpoints автоматически.

**Observability/evidence:** witness digests включаются в общий bundle вместе с receipt digests, но не переписывают существующий подписанный product proof contract без отдельной нужды. Успех UI при несовпавшем witness остаётся FAIL; hashes без DB/release binding недостаточны.

### Task T05 — две независимые подписанные identity и hostile owner boundary

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/owners.py`, `apps/api/tests/test_restoration_qa_owners.py`; Modify new `runner.py`, `fixtures.py`; Read `apps/orchestrator/templates/max-miniapp-nextjs/src/lib/max/validate-init-data.ts`, `src/lib/max/session.ts`, `src/app/api/max/session/route.ts` внутри того же template; Read/Test `apps/orchestrator/src/omnia_orchestrator/services/restoration_adaptation_health.py`, `apps/orchestrator/tests/test_restoration_adaptation_health.py`.

**Interfaces:** `open_signed_actor(manifest: QaManifest, actor: Literal['A','B']) -> httpx.AsyncClient`; `check_owner_boundary(manifest: QaManifest) -> list[CheckResult]`. A/B clients имеют отдельные cookie jars; QA-only bot token никогда не является production bot token. Generated fixture использует настоящий `validateMaxInitData` и `/api/max/session`, без injected `x-omnia-user-id` как доказательства подписи.

- [ ] 1: failing tests `test_two_signed_actors_use_distinct_cookie_jars`, `test_tampered_and_expired_launch_rejected`, `test_actor_b_cannot_patch_or_delete_actor_a`, `test_owner_body_override_does_not_change_authority`.
- [ ] 2: `uv run --frozen pytest -q tests/test_restoration_qa_owners.py`; initial red missing module. Подпись строится отдельным helper по canonical sorted launch fields:

```python
secret = hmac.new(b'WebAppData', qa_bot_token.encode(), hashlib.sha256).digest()
signature = hmac.new(secret, canonical_launch.encode(), hashlib.sha256).hexdigest()
# POST /api/max/session с {'initData': encoded_fields_with_signature}.
# A/B используют разные user.id и query_id, свежий auth_date.
```

- [ ] 3: токен генерировать для нового isolated QA runtime, хранить в private env и уничтожить при cleanup. Во время production-disposable теста конфигурация принадлежит только новому QA project; не менять токен существующего приложения и не слать реальные MAX сообщения.
- [ ] 4: A видит A1/A2, B видит B1; B GET/PATCH/DELETE A1 получает согласованный 404 либо явный 403 по fixture contract; SQL witness A1 неизменен. Anonymous → 401; cookie B + JSON `owner_id=A` не даёт полномочий; guessed UUID не раскрывает payload.
- [ ] 5: wrong key, изменённый user.id, duplicate query key, expired/future timestamp и replay after session expiration не проходят. Разрешённое штатное повторное использование валидного launch не объявлять security bug само по себе; проверять фактический TTL template contract.
- [ ] 6: `uv run --frozen pytest -q tests/test_restoration_qa_owners.py` плюс orchestrator `uv run --frozen pytest -q tests/test_restoration_adaptation_health.py`; запустить `uv run --frozen python -m omnia_api.ops.restoration_qa.cli owners --manifest "$QA_MANIFEST"` до/после activation.
- [ ] 7: выполнить D, commit `test(qa): verify signed cross-owner restoration boundaries`.

**Observability/evidence:** actor labels A/B, HTTP codes, row-set/value digests, auth reason codes; без cookies, signatures, bot token или launch body. Если QA signer недоступен, статус BLOCKED_ENV; поддельный owner header не заменяет acceptance.

### Task T06 — исторический UI/API: create/read/update/reload/delete

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/crud.py`, `apps/api/tests/test_restoration_qa_crud.py`, `apps/web/src/lib/__tests__/restoration-qa-crud.test.tsx`; Modify new `fixtures.py`, `runner.py`, `v1.json`, `v2.json`. Read `apps/web/src/components/max/MaxRestorationPanel.tsx`, `apps/web/src/lib/__tests__/max-restoration.test.tsx`.

**Interfaces:** `check_crud(manifest: QaManifest) -> list[CheckResult]`; consumes T04 witness and T05 A/B sessions. Fixture API `/api/qa-tasks`, `/api/qa-tasks/{id}`; create returns 201 with server ID, update 200, own delete 204, repeated GET 404. Historical form label «Название» writes current `summary` via approved adapter.

- [ ] 1: failing tests `test_historical_form_creates_current_required_summary`, `test_update_preserves_hidden_columns_and_json`, `test_reload_uses_new_connection`, `test_delete_removes_only_new_synthetic_row`. Не считать проверкой form test, который только смотрит mock-call без SQL результата.
- [ ] 2: red `uv run --frozen pytest -q tests/test_restoration_qa_crud.py`; web `pnpm test -- src/lib/__tests__/restoration-qa-crud.test.tsx`. Ожидаемый mutation red: старый POST без `summary` → SQL constraint/400, hidden JSON replacement → witness mismatch.
- [ ] 3: script создаёт A3 через historical contract, читает новой HTTP-сессией A, обновляет visible value, закрывает соединение, выполняет reload GET; проверяет DB witness и returned representation. Использовать `Cache-Control: no-cache`, unique request marker; stale UI cache не считается reload.
- [ ] 4: удалить только A3 созданную текущим run; подтвердить 204, subsequent GET 404, SQL отсутствие A3, A1/A2/B1/dependent rows без изменений. Повтор DELETE получает contract 404 и не меняет данные.
- [ ] 5: негативные случаи: пустое summary, missing ID, unknown JSON keys, чужой ID/owner, Unicode, duplicate submit; assertions: validation 4xx без частичной записи, unknown fields не теряются в существующей строке, duplicate submit соответствует выбранному API contract, без лишних A1 mutations.
- [ ] 6: автоматический browser/render check на fixture: видна старая форма, submit выполняет правильный DTO, reload сохраняет значение; сохранить screenshot только synthetic UI. Реальный E2E HTTP/SQL остаётся независимым от Vitest mock.
- [ ] 7: green указанных commands, `uv run --frozen python -m omnia_api.ops.restoration_qa.cli crud --manifest "$QA_MANIFEST"`, выполнить D, commit `test(qa): prove restored historical CRUD against current schema`.

**Observability/evidence:** per-step status, request method/path template без row values, SQL before/after digests. Не расширять delete на baseline A1/A2/чужие проекты. Конкретный product adapter fix допускается только после failure свежего generated candidate; не переписывать engine ради fixture.

### Task T07 — restart приложения и Project Cell после активации

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/restart.py`, `apps/api/tests/test_restoration_qa_restart.py`; Modify new `runner.py`; Test existing `apps/orchestrator/tests/test_code_restoration_engine.py`, `apps/orchestrator/tests/test_restoration_adaptation_activation_effects.py`; Read `apps/api/src/omnia_api/routers/runtime.py`, `apps/orchestrator/scripts/smoke_code_restoration.py`.

**Interfaces:** `check_restart(manifest: QaManifest, target: Literal['app','cell']) -> list[CheckResult]`; uses normal runtime lifecycle and manifest-owned container IDs. Product shared API/worker/orchestrator restart не является частью этого шага — он только в isolated fault stack T09.

- [ ] 1: `test_restart_refuses_foreign_container_and_volume`, `test_app_restart_preserves_database_and_commit`, `test_cold_cell_resume_preserves_rows_and_owner_boundary`; initial red missing module.
- [ ] 2: `uv run --frozen pytest -q tests/test_restoration_qa_restart.py`; implement exact ownership gate: project/workspace/resource labels, no active apply, one current snapshot; container name prefix без manifest недостаточен.
- [ ] 3: перезапустить только app process/container собственного synthetic проекта, дождаться bounded health. Сверить actual code commit, DB volume identity, T04 witness и A/B read/auth.
- [ ] 4: штатный stop/suspend → start/resume Project Cell, не `docker rm` БД и не copy old volume. Повторить health, artifact identity, A1/A2/B1 SQL values, fresh A session, B denial.
- [ ] 5: negative stale runtime pointer/foreign volume/restart while activation ambiguous → stop и reconcile, без разрушения container. `test_restart_does_not_fallback_to_old_source_after_admitted_effects` проверяет отсутствие `start_source_writers`.
- [ ] 6: green API test + orchestrator `uv run --frozen pytest -q tests/test_code_restoration_engine.py tests/test_restoration_adaptation_activation_effects.py`; live `uv run --frozen python -m omnia_api.ops.restoration_qa.cli restart --manifest "$QA_MANIFEST" --target app`, затем та же команда с `--target cell`.
- [ ] 7: выполнить D, commit `test(qa): verify post-restoration restart and cold resume`.

**Observability/evidence:** before/after container incarnation digests, stable DB volume identity, serving commit, health and witness; incarnation может измениться, данные/правильный artifact — нет. Не использовать успешный platform health как restart proof app.

### Task T08 — receipt/lineage и ровно один terminal settlement

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/receipts.py`, `apps/api/tests/test_restoration_activation_completion.py`, `apps/api/tests/test_restoration_qa_receipts.py`; Modify only if regression proves defect `apps/api/src/omnia_api/services/restorations.py`, `apps/api/src/omnia_api/services/generation/publication.py`; Read `apps/api/src/omnia_api/models/restoration.py`, `models/project_version.py`, `models/wallet_charge.py`, `services/project_versions.py`, `services/generation_runs.py`.

**Interfaces:** `check_receipts(manifest: QaManifest, operation_id: UUID) -> list[CheckResult]`; consumes controller status, `Restoration` SQL projection, GenerationRun, Snapshot/ProjectVersion and QA-only billing counters. Existing function under test: `_complete_restoration_adaptation_activation(session, *, run_id, command, receipt, files) -> bool`.

- [ ] 1: DB-backed `test_complete_activation_twice_creates_one_snapshot_and_settlement`, `test_concurrent_callbacks_lock_one_terminal_transition`, `test_receipt_binding_mismatch_never_advances_history`.
- [ ] 2: initial red must demonstrate missing **integrated assertion**, or injected duplicate-settlement mutation. Do not claim current code duplicates charges: it already uses locks, deterministic snapshot UUID and `activation_settled_at` guard.
- [ ] 3: use real disposable DB and two independent sessions:

```python
async def test_complete_activation_twice_is_idempotent(completion_fixture):
    await completion_fixture.complete_in_new_session()
    await completion_fixture.complete_in_new_session()
    facts = await completion_fixture.read_sql_facts()
    assert facts['current_snapshot_count'] == 1
    assert facts['versions_for_generation_run'] == 1
    assert facts['snapshots_for_operation'] == 1
    assert facts['quota_consumption_delta'] == (1 if facts['is_free'] else 0)
    assert facts['terminal_settlement_count'] == 1
    assert facts['duplicate_wallet_settlement_count'] == 0
```

`completion_fixture` builds the existing Restoration/GenerationRun/ProjectVersion/Message/workspace/receipt bindings, not a replacement fake finalizer. Test both `is_free=True` and paid dispatch: free quota delta 1 only for free run, delta 0 for paid run; terminal settlement transition remains one in both. Wallet may contain several legitimate per-model usage entries: requirement is one terminal settlement and **no duplicated logical charge**, not arbitrary `COUNT(wallet_charges)=1` for all provider usage. SQL `activation_settled_at IS NOT NULL` is checked together with before/after quota/wallet deltas, not used alone as proof of exactly-once charging.

- [ ] 4: receipt verifier checks operation/project/owner/workspace/run/source snapshot/base draft/target commit/candidate artifact/proof/activation request/receipt digests, source/target epochs; observed serving code equals planned commit. `completed` без activated receipt или applied snapshot → FAIL.
- [ ] 5: source historical version immutable; new adapted version has `restored_from_snapshot_id=source_snapshot_id`, current snapshot points to deterministic adapted snapshot, generation run and version binding singular. Exact restoration uses its own existing lineage, not fabricated generation association.
- [ ] 6: negative changed owner/fence/current draft/receipt digest/planned commit → 409 and zero effects/history increments; same callback → same result; nullable notification state can reconcile without repeated billing. Add `test_notification_retry_does_not_resettle`.
- [ ] 7: `uv run --frozen pytest -q tests/test_restoration_activation_completion.py tests/test_restoration_qa_receipts.py tests/test_restorations.py`; `uv run --frozen python -m omnia_api.ops.restoration_qa.cli receipts --manifest "$QA_MANIFEST"`. Operation ID берётся из связанного runner journal; произвольный ID извне не принимается.
- [ ] 8: выполнить D, commit `test(restorations): prove terminal activation and settlement idempotency`.

**Observability/evidence:** exact binding/digest map and aggregate deltas of only QA principal; no wallet description/user messages. SQL witness and receipt independent: neither alone substitutes the other.

### Task T09 — пять отказов с forward recovery до финальных свидетелей

**Files:** Create `apps/api/tests/test_restoration_fault_recovery_e2e.py`, `apps/api/src/omnia_api/ops/restoration_qa/faults.py`; Modify tests `apps/api/tests/test_max_finalization.py`, `apps/api/tests/test_generation_worker.py`, `apps/api/tests/test_restoration_background_reconciliation.py`, `apps/orchestrator/tests/test_restoration_adaptation_activation.py`, `apps/orchestrator/tests/test_restoration_adaptation_activation_service.py`, `apps/orchestrator/tests/test_restoration_adaptation_activation_effects.py`; conditional product fixes only `apps/api/src/omnia_api/services/max_finalization.py`, `services/restorations.py`, `services/restoration_reconciliation.py`, `apps/api/src/omnia_api/workers/generation.py`, orchestrator activation modules named above.

**Interfaces:** `run_fault_case(manifest: QaManifest, point: FaultPoint) -> list[CheckResult]`, enum exact values in matrix. Injection lives in test dependency adapters/process supervisor, not public endpoint or globally enabled production env. Production-disposable mode rejects process-kill cases against shared services.

| Point | Exact boundary | Existing regression to retain | New test and necessary assertion | Permitted rehearsal |
|---|---|---|---|---|
| `api_after_proof_before_commit` | controller accepted proof_ready, API session before durable commit | `test_max_finalization.py` sql-commit-fault case | `test_api_crash_after_proof_recovers_to_completed`: new API session, redispatch same proof, continue through activated + SQL lineage + settlement | Kill only isolated API process on disposable stack; live production only non-destructive lost-response observation |
| `worker_after_durable_offer` | offer journal persisted, generation worker before projection/ack | `test_claimed_proof_ready_offer_handoff_waits_for_reconciliation` | `test_worker_offer_handoff_survives_restart_to_completed`: `adaptation_activation_handoff_pending` command journal survives, no orphan fail/no second generation | Kill only isolated worker; own synthetic operation |
| `orchestrator_during_activation` | after effects admission/source writers stop, before terminal receipt | `test_recover_replays_each_durable_phase_forward`, `test_recover_all_uses_bound_request_and_skips_completed_effects`, effects post-PONR retry | `test_real_restart_recovers_admitted_activation_without_old_writers`: fresh service + real persisted journal + recover_all → receipt, SQL completion | Dedicated orchestrator process; no shared production restart for fault injection |
| `lost_accepted_apply_response` | apply accepted/persisted, client transport drops response | `test_uncertain_effect_restart_observes_without_reapplying` | `test_lost_apply_response_observes_same_activation_and_settles_once`: observe/replay exact intent, no new fence/candidate/charge | HTTP fault proxy scoped to disposable project route |
| `duplicate_callback` | same activated receipt delivered concurrently/twice | `test_duplicate_activation_returns_receipt_without_second_effect` | `test_duplicate_callback_reaches_one_version_and_one_settlement`: two API sessions plus delayed duplicate, same snapshot/version | Isolated replay of signed/internal fixture receipt; never expose callback bypass |

- [ ] 1: сохранить existing recovery suite как baseline; добавить новые integration test names из матрицы. Red считается missing end-to-end terminal assertion; отдельно controlled mutation (drop schedule/replay settlement/start old source) обязана заставить соответствующий test упасть.
- [ ] 2: implement supervisor checkpoints with file/socket handshake `{run_id,point,operation_id,activation_id}`. Kill допускается только после durable checkpoint подтверждения; random sleeps не являются точной fault injection.
- [ ] 3: для каждого point новый disposable project/database/journal. Candidate может быть детерминированно подготовлен в CI без платного AI, но проходит настоящие proof/activation/services; T03 отдельный real model run доказывает generator path.
- [ ] 4: после каждого crash запустить **новый процесс/соединение**, а не тот же объект в памяти; observe/reconcile без client GET до terminal. Проверить `recover_all()` по одному corrupt/unrelated journal: чужая ошибка не мешает valid QA activation и не удаляет его archive.
- [ ] 5: общий assert для всех пяти:

```python
assert operation.state == 'completed'
assert operation.activation_effects_admitted is True
assert serving_commit == operation.adaptation_planned_commit_sha
assert active_version_count == 1
assert versions_for_run == 1
assert terminal_settlement_delta == 1
assert duplicate_logical_charge_count == 0
assert old_source_writer_starts_after_admission == 0
assert independent_witness.status == 'PASS'
assert owner_boundary.status == 'PASS'
```

- [ ] 6: pre-admission cancel отдельный negative: cancelled + no effects + zero terminal consumption; after admission cancel → 409/recovery, не old-code rollback. Invalid receipt, foreign binding, changed fence, lost network сохраняют uncertainty и scheduled reconciliation.
- [ ] 7: API `uv run --frozen pytest -q tests/test_restoration_fault_recovery_e2e.py tests/test_restoration_activation_completion.py tests/test_max_finalization.py tests/test_generation_worker.py tests/test_restoration_background_reconciliation.py`; orchestrator `uv run --frozen pytest -q tests/test_restoration_adaptation_activation.py tests/test_restoration_adaptation_activation_service.py tests/test_restoration_adaptation_activation_effects.py`.
- [ ] 8: Linux `uv run --frozen python -m omnia_api.ops.restoration_qa.cli faults --manifest "$QA_MANIFEST" --all`; каждый point отдельный result/evidence digest, pass только 5/5 без `skip`. `BLOCKED_ENV` не округляется до PASS.
- [ ] 9: выполнить D, commit `test(restorations): cover five forward-recovery fault boundaries`. Если найден product bug, commit называет исправленный boundary; повторяется затронутый fault и полная пятистрочная matrix на новом SHA.

**Observability/evidence:** durable before/after phase, process incarnations, timestamps, attempts, monotonic fence/receipt progression, effect counters; не удалять зависшую admission ради зелёного cleanup. Allowed production rehearsal — только synthetic disposable project и безопасный scoped response loss/duplicate normal request; destructive process cases только отдельный stack.

## 5. P1 work packages

### Task T10 — один deterministic QA profile для empty и compatible

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/automatic.py`, `apps/api/tests/test_restoration_qa_automatic.py`; Modify new `cli.py`, `fixtures.py`; Test existing `apps/api/tests/test_restoration_background_reconciliation.py`, `apps/orchestrator/tests/test_restoration_empty.py`, `apps/orchestrator/tests/test_restoration_empty_materializer.py`, `apps/orchestrator/tests/test_code_restoration_engine.py`.

**Interfaces:** `run_automatic_profile(release_sha: str) -> list[CheckResult]` creates exactly two independently owned fixtures and runs one command `uv run --frozen python -m omnia_api.ops.restoration_qa.cli automatic --release-sha "$RELEASE_SHA" --evidence-root "$QA_ROOT"`; explicit generation caller is forbidden in this profile.

- [ ] 1: tests `test_empty_and_compatible_profile_never_dispatches_ai`, `test_technical_rows_do_not_make_business_nonempty`, `test_unmeasured_inventory_is_never_empty`, `test_first_write_after_empty_restore_works`. Inject AI client that raises AssertionError on any call.
- [ ] 2: red `uv run --frozen pytest -q tests/test_restoration_qa_automatic.py`; initial missing profile; mutation treating `not_measured` as zero must fail.
- [ ] 3: empty: technical/auth rows allowed, business zero; automatic preparation chooses verified empty policy, materializes target safely, completes, first signed A create/reload works. Verify `replace_verified_empty` receipt has required witness/artifact digests when chosen; do not assume current database identity must remain if verified-empty replacement is the explicit contract.
- [ ] 4: compatible: current synthetic A/B rows exist, historical source structurally/behaviorally compatible; automatic exact completes, source current DB identity stable, witness and owner boundary PASS.
- [ ] 5: both branches assert `generation_runs_delta=0`, `AI_dispatch_count=0`, `quota_consumption_delta=0`, `wallet_charge_delta=0`; fixture provisioning likewise zero. Only restoration-created technical version is expected; no fake generation run to seed history.
- [ ] 6: source schema changes between ready/apply, inventory read fails, competing owner request, double submit, stale snapshot → safe reject/reconcile, no AI fallback. Add `test_automatic_never_silently_enters_adaptive`.
- [ ] 7: green API command + orchestrator `uv run --frozen pytest -q tests/test_restoration_empty.py tests/test_restoration_empty_materializer.py tests/test_code_restoration_engine.py`; one `automatic` invocation produces two branch results with identical release SHA.
- [ ] 8: выполнить D, commit `test(qa): automate empty and compatible restoration acceptance`.

**Observability/evidence:** single profile index, per-branch manifest, mode/strategy, first-write witness, no-AI counters. 20.09 live PASS remains baseline; this is a bounded regression, not recreation of the earlier manual study.

### Task T11 — semantic compatibility и реальные последствия маршрутов

**Files:** Create `apps/api/src/omnia_api/ops/restoration_qa/semantics.py`, `apps/api/tests/test_restoration_qa_semantics.py`, `apps/orchestrator/tests/test_restoration_semantic_contract.py`, `apps/api/tests/fixtures/restoration_qa/semantic_cases.json`; Modify if supported case fails `apps/orchestrator/src/omnia_orchestrator/services/versioning/compatibility.py`, `apps/orchestrator/src/omnia_orchestrator/services/restoration_data_contract.py`, `apps/api/src/omnia_api/services/versioning_capabilities.py`; retain tests `apps/orchestrator/tests/test_versioning_capability_parity.py`, `apps/api/tests/test_versioning_capabilities_v4.py`.

**Interfaces:** `check_semantics(manifest: QaManifest, case_id: str) -> CheckResult`; matrix cases classify `supported_preserve`, `explicit_decision_required`, `unsupported`. UNKNOWN semantics cannot yield automatic compatible.

- [ ] 1: parameterized red cases `json_hidden_merge`, `minor_major_units`, `renamed_required_field`, `user_function_default`, `trigger_side_effect`, `hidden_cascade_delete`, `same_route_wrong_shape`, `same_route_missing_owner_filter`, `external_notification`. Assertions supplied by matrix, not self-authored generated tests alone.
- [ ] 2: precise behavior: JSON PATCH visible key preserves nested unknown keys; `amount_minor=12345` never becomes `12345.00` major units silently; unsupported unit conversion → `needs_changes`/explicit approved mapping before writes; trigger/function/cascade unsupported → blocker identifying object and operation.
- [ ] 3: add fixture event/outbox sink with network disabled; read/proof/retry must produce zero external action calls. Approved synthetic create produces exactly one synthetic event; duplicate request/activation must not duplicate it. Do not use payment or notification production credentials.
- [ ] 4: route parity mutation keeps GET/POST exports but returns wrong shape or removes owner predicate; behavioral tests fail despite capability parity PASS. Preserve current golden corpus extractor tests, no new parallel route parser.
- [ ] 5: minimal implementation adds named conservative compatibility blockers only when required; never infer arbitrary semantic conversion from SQL types. For unsupported runtime/custom launch/service, explicit actionable `needs_changes`, unchanged source/data.
- [ ] 6: API `uv run --frozen pytest -q tests/test_restoration_qa_semantics.py tests/test_versioning_capabilities_v4.py`; orchestrator `uv run --frozen pytest -q tests/test_restoration_semantic_contract.py tests/test_versioning_capability_parity.py tests/test_restoration_data_contract.py`.
- [ ] 7: `uv run --frozen python -m omnia_api.ops.restoration_qa.cli semantics --manifest "$QA_MANIFEST" --all`; bundle supported cases PASS and unsupported safe-refusal PASS as separate expectations; no claim of universal SQL/business semantic proof.
- [ ] 8: выполнить D, commit `test(restorations): enforce behavioral semantic compatibility boundaries`.

**Observability/evidence:** reason_code/object/operation/decision, schema and value digests, denied egress count, synthetic event delta. Safe refusal is successful policy enforcement, not successful restoration.

### Task T12 — воспроизводимые Windows/Node и real Docker Linux проверки

**Files:** Modify `apps/orchestrator/tests/test_backup_cells.py`, `apps/orchestrator/pyproject.toml`, `apps/web/vitest.config.ts`, `apps/web/package.json`, `.github/workflows/ci.yml`; Create `apps/web/src/test/webstorage.ts`, `apps/web/src/lib/__tests__/webstorage-harness.test.ts`, `.github/workflows/restoration-runtime-qa.yml`, `apps/api/tests/test_restoration_runtime_qa_workflow.py`; Read/retain `apps/orchestrator/scripts/smoke_code_restoration.py`, `apps/orchestrator/tests/_versioning_pg.py`.

**Interfaces:** workflow dispatch inputs `release_sha`, `profile=automatic|faults|all`; exact checkout SHA, Linux Docker host, isolated services; outputs complete `environment.json` and CheckResults. Windows has explicit `posix_permissions` marker and portable non-POSIX assertions; real Docker QA remains mandatory on Linux.

- [ ] 1: split `test_manifest_schema_and_private_file_modes`: portable `test_manifest_schema_and_secret_redaction` always runs, strict `test_private_file_modes_posix` marked `posix_permissions` and skipped only `os.name!='posix'`; validate full 0700 dir/0600 file and no secret payload on Linux.
- [ ] 2: red/green Windows targeted command `uv run --frozen pytest -q tests/test_backup_cells.py`; expected original red 0777/0700 eliminated by scoped platform marker, not by weakening production permission code. Linux `uv run --frozen pytest -q -m posix_permissions tests/test_backup_cells.py` must execute, not skip.
- [ ] 3: add WebStorage test that exercises `window.localStorage`/`sessionStorage` and throws if Node global shadows jsdom. Prefer pinned Node 20 (existing web CI) for authoritative tests; for Node 25 compatibility bootstrap define jsdom-owned storage in test setup before import. Do not globally pass an unsupported Node option to Node 20.
- [ ] 4: `pnpm test -- src/lib/__tests__/webstorage-harness.test.ts src/lib/__tests__/max-restoration.test.tsx src/lib/__tests__/max-adaptation.test.tsx`; record Node 20 baseline and Node 25 compatibility separately. Temporary diagnostic PowerShell `$env:NODE_OPTIONS='--no-experimental-webstorage'` is evidence, not permanent proof of portability.
- [ ] 5: new Linux workflow installs frozen dependencies, PostgreSQL 16/Redis 7, template lockfiles/images and real Docker; creates mode-0700 QA root under runner temp. Run from orchestrator: `uv run --frozen python scripts/smoke_code_restoration.py --qa-parent "$QA_PARENT" --cleanup-on-success`; existing script flags verified, no invented Windows support.
- [ ] 6: add workflow test `test_runtime_qa_requires_linux_docker_and_exact_sha`, `test_runtime_qa_uploads_failures_and_cleanup_result`, `test_missing_docker_is_blocked_env_not_pass`. A missing daemon fails required job; do not quietly mock the engine or mark real E2E optional.
- [ ] 7: Windows fallback means dispatch exact-SHA Linux job and retrieve artifacts; not repairing registry by guess, not using production business DB, not reporting CI unit DB as Docker E2E. Required matrix includes new runner T09/T10 with artifact retention contract.
- [ ] 8: `uv run --frozen pytest -q tests/test_restoration_runtime_qa_workflow.py` from API; `actionlint .github/workflows/ci.yml .github/workflows/restoration-runtime-qa.yml`; perform D, commit `test: make restoration QA portable and require real Linux runtime evidence`.

**Observability/evidence:** OS, Python/Node/pnpm/Docker versions, service readiness, exact SHA, executed/skipped markers and reason. Broken local Docker stays documented BLOCKED_ENV until actually repaired; remote PASS names remote environment explicitly.

### Task T13 — phase/recovery telemetry, alerts и безопасный operator retry

**Files:** Create `apps/api/src/omnia_api/services/restoration_observability.py`, `apps/api/tests/test_restoration_observability.py`, `apps/api/src/omnia_api/ops/restoration_status.py`, `apps/api/tests/test_restoration_operator_retry.py`, `docs/operations/restoration-monitoring.md`; Modify `apps/api/src/omnia_api/services/restoration_reconciliation.py`, `services/restorations.py`, `services/generation_deadline.py`, `apps/web/src/components/max/MaxRestorationPanel.tsx`, `apps/web/src/lib/use-max-restoration.ts`, `apps/web/src/lib/__tests__/max-restoration.test.tsx`; Read existing `apps/api/src/omnia_api/services/generation_metrics.py`.

**Interfaces:** `restoration_status` emits aggregate JSON view `phase_counts`, `oldest_due_seconds`, `activation_age_seconds`, `recovery_attempts`, `duplicate_callbacks`, `settlement_mismatches`, `deadline_stage_counts`; no per-user labels in aggregate metrics. `build_retry_request(failed_operation, current_snapshot_id) -> RestoreRequest` creates new idempotency key and current binding, never reopens terminal row.

- [ ] 1: tests `test_stuck_due_reconcile_alert_after_120_seconds`, `test_controller_outage_does_not_terminalize_operation`, `test_settlement_mismatch_is_immediate_critical`, `test_metrics_never_contain_prompt_or_business_values`; red missing status aggregator.
- [ ] 2: emit structured transition fields `restoration_operation_id`, `generation_run_id`, `activation_id`, phase, deadline stage, retry reason, release SHA; raw prompt/SQL/body/credentials forbidden. Keep existing backlog log; collector does not change restoration state.
- [ ] 3: create operator dashboard from same JSON: current counts by phase, oldest due and ages, failures grouped by safe reason. Use existing admin/operations mechanism or CLI table first; no Prometheus/Grafana dependency merely to draw dashboard. Document refresh every 30s and source freshness.
- [ ] 4: alert definitions: due reconcile >120s warning; >300s critical; admitted activation with no progress >120s warning; configured proof/activation deadline exceeded critical; duplicate callback informational unless settlement/version mismatch; mismatch immediate critical; release drift critical; cleanup expired >24h warning. Dedupe by safe reason + activation digest, recovery resolves the same incident.
- [ ] 5: retry tests: terminal failed before effects → new preparation from same selected version/current snapshot; old key/draft never reused; terminal cancelled can start new op by explicit user action; admitted/uncertain activation disables retry and shows recovery. Browser reload/duplicate click one new operation, one generation on explicit adapt.
- [ ] 6: show «Повторить откат» only when safe; API admission remains authoritative. Busy locks return controlled retryable 409/503 and `Retry-After`, panel re-observes after terminal; historical fixed lock traceback is not reintroduced.
- [ ] 7: API `uv run --frozen pytest -q tests/test_restoration_observability.py tests/test_restoration_operator_retry.py tests/test_restoration_background_reconciliation.py tests/test_generation_deadline.py`; web `pnpm test -- src/lib/__tests__/max-restoration.test.tsx` and typecheck.
- [ ] 8: trigger synthetic stuck/duplicate/mismatch in isolated stack, capture alert + resolution, verify no external user message sent by QA. Perform D, commit `feat(ops): expose restoration recovery status and safe retry`.

**Observability/evidence:** dashboard samples, alert onset/resolution, retry new/old IDs. Existing failed operation remains immutable; alerting never initiates a paid retry or destructive cleanup automatically.

### Task T14 — production checkout hygiene, release gate и durable cleanup

**Files:** Create `infra/release/restoration-release-gate.py`, `apps/api/tests/test_restoration_release_manifest.py`, `docs/operations/restoration-release-runbook.md`; Modify `.github/workflows/restoration-runtime-qa.yml`, `.github/workflows/production-smoke.yml`, `.github/workflows/production-generation-canary.yml`, `apps/api/src/omnia_api/ops/restoration_qa/evidence.py`, `cli.py`; Read production full Compose and `infra/release/README.md`. Existing general release README is historical migration-specific; never copy its 0048-only downgrade/preflight example as current restoration recovery.

**Interfaces:** `validate_release_bundle(path: Path, release_sha: str) -> list[str]`; exit 0 only all mandatory current-SHA checks PASS. `cleanup_owned` consumes resource journal persisted before allocation; returns per-resource deleted/preserved/retry/error without secrets. No broad directory cleanup.

- [ ] 1: read-only inventory five tracked document paths and 123 untracked paths on server: status, type, owner, mode, byte count, SHA-256 for files, symlink target classification. No contents printed. Existing dirty checkout blocks source edits/deploy until its preservation procedure completes.
- [ ] 2: produce explicit allowlist relocation manifest under `/opt/omnia-runtime/releases/` with original/destination/checksum/mode. Separate secret-bearing `.env` histories/backups from documents. Verify destination private and paths remain inside named roots; never recursive delete, `git clean`, blanket `git restore`, stash or reset.
- [ ] 3: obtain named authorization for moving the listed owner artifacts if not already covered by the session; approvals attach to concrete manifest. Preserve tracked document edits as a **local preservation commit on the existing server branch**, with only the five reviewed document paths staged; never commit secrets/untracked env histories. Record preservation commit ID and branch, do not push those unrelated edits to main.
- [ ] 4: move only approved untracked artifacts to private release archive with checksum and mode verification. After clean status, switch to tracking `main` safely and fast-forward `origin/main`; if existing main diverges or switching would overwrite files, stop and preserve state. No forced checkout. Record original branch and preservation ref so owner work remains recoverable.
- [ ] 5: tests `test_release_gate_rejects_dirty_or_nonmain_source`, `test_bundle_rejects_mixed_sha_and_missing_fault`, `test_bundle_rejects_skipped_mandatory_e2e`, `test_cleanup_refuses_foreign_or_admitted_resource`, `test_cleanup_recovers_after_runner_crash`. Red before gate; inject stale SHA/missing checkpoint after implementation to prove fail-closed.
- [ ] 6: validator requires exact CI/smoke/current release identities, T03–T11 mandatory results, redaction/cleanup status and all five fault points. Unit evidence and previous successful v8 cannot satisfy current protected chain; historical baseline retained in separate field.
- [ ] 7: workflow after verified deploy updates expected SHA vars, runs smoke, deterministic `automatic`, fault stack, and explicit bounded adaptive canary for restoration-affecting release; upload artifacts `if: always()`. Scheduled smoke every five minutes remains; adaptive generation is not scheduled every five minutes and must not silently spend quota indefinitely. Use one explicit adaptive acceptance per relevant release; retry only after diagnosed change.
- [ ] 8: durable cleanup sweeper reads only runner-owned expired journals, confirms owner/project/resources and no active/admitted unresolved operation, uses existing owner project delete with bounded 409/503 retry. Tests preserve primary failure and return cleanup failure separately; never cancel others to clear capacity.
- [ ] 9: API `uv run --frozen pytest -q tests/test_restoration_release_manifest.py tests/test_restoration_release_gate.py tests/test_versioning_cleanup_retry.py`; root `actionlint`; dry-run gate on incomplete bundle must return nonzero, complete synthetic bundle exit 0.
- [ ] 10: perform D, commit `ci(ops): enforce restoration release evidence and owned cleanup`; production source clean/main, pushed SHA deployed using full Compose, host orchestrator identity exact, health + external smoke evidence captured.

**Observability/evidence:** before/after server status, preservation branch/commit and archive manifest, no file contents; automation logs gate reasons and missing check IDs. If authorization for named moves is unavailable, T14 remains BLOCKED and local/isolated test development may continue in a separate clean checkout, but production delivery is not claimed.

### Task T16 — NEW-25: мастер не навязывает seed вопреки описанию владельца

**Files:** Modify `apps/web/src/lib/max-brief.ts`; Test `apps/web/src/lib/__tests__/max-brief.test.ts`; Read `apps/web/src/components/max/MaxStudio.tsx`; live evidence of LIVE-01 updated in this plan by execution owner. Не менять backend data contract, пока причина ограничена prompt composition.

**Interfaces:** Существующая `buildMaxProjectPrompt(brief: MaxProjectBrief): string` сохраняет полное пользовательское `idea` и MAX/auth требования; общий boilerplate больше не требует demo data. Явное желание владельца иметь демонстрационные данные остаётся в его `idea`; новой эвристики распознавания отрицаний и нового переключателя не требуется.

- [ ] 1: добавить regression на текущий подтверждённый defect:

```typescript
it("does not override an explicit empty business database requirement", () => {
  const idea = "Создай список задач. Без seed и демонстрационных данных. База изначально пустая.";
  const prompt = buildMaxProjectPrompt({
    name: "Rollback QA", idea, appType: "custom", audience: "",
    primaryAction: "", features: [], style: "clean", brandColors: "",
  });
  expect(prompt).toContain(idea);
  expect(prompt).not.toContain("реальные русские тексты и демонстрационные данные");
});
```

- [ ] 2: `pnpm test -- src/lib/__tests__/max-brief.test.ts`; expected red — `not.toContain` fails на безусловной строке 114. Это проверяет реальный composer, не макет UI.
- [ ] 3: заменить конец общего предложения на «реальные русские тексты.»; оставить empty/loading/error states и остальные MAX ограничения. Не добавлять вторую конфликтующую инструкцию «без seed» после прежней: источник противоречия должен исчезнуть.
- [ ] 4: добавить `preserves explicitly requested demonstration data in user idea`, `keeps empty initial state requirement at the end of a long brief`; assertions: исходный idea сохранён целиком, ограничение длины продолжает действовать, boilerplate не добавляет свой seed. Убедиться, что два вызова composer в `MaxStudio` получают один контракт до/после secret scrub.
- [ ] 5: targeted green + `pnpm typecheck` + `pnpm build`; выполнить D, commit `fix(web): preserve explicit no-seed requirements in MAX briefs`.
- [ ] 6: fresh synthetic user-path creation через мастер после deploy; сохранить accepted request без секрета, runtime terminal и initial business count 0 до первой записи пользователя. Если seed всё ещё появляется, новый defect относится к generator/fixture исполнения, его нельзя считать тем же уже исправленным composer bug.

**Observability/evidence:** LIVE-01 остаётся исходным failing proof; remediation rerun получает новый project/run ID и свой exact SHA. Проверка count ограничена synthetic QA project; не читать реальные бизнес-строки. T16 выполняется в W2 до повторного сценария initial-empty, но не блокирует независимую диагностику T01.

## 6. P2 work package

### Task T15 — непрерывный UX адаптации и неизменяемая terminal диагностика

**Files:** Modify `apps/web/src/components/max/MaxRestorationPanel.tsx`, `apps/web/src/lib/use-max-adaptation.ts`, `apps/web/src/lib/use-max-restoration.ts`, `apps/web/src/lib/__tests__/max-adaptation-one-click.test.tsx`, `apps/web/src/lib/__tests__/max-adaptation.test.tsx`, `apps/web/src/lib/__tests__/prompt-stream-adaptation.test.tsx`; conditional Modify/Test `apps/api/src/omnia_api/services/generation_deadline.py`, `apps/api/tests/test_generation_deadline.py` if terminal snapshot defect reproduces.

**Interfaces:** Preserve current `MaxAdaptationAttachment` with projectId/prompt/reference/phase and existing `onPrepareAdapt`/`onAdapt` callbacks. Do not redesign durable intent: cancel acknowledgement transitions attachment to ready, one continuation uses existing idempotency key. Terminal diagnostic snapshot separates restoration ID, nullable tool operation ID, stage and final step count.

- [ ] 1: add `test_cancel_ack_auto_continues_same_adaptation_once`, `test_reload_during_cancel_preserves_intent`, `test_old_project_callback_cannot_start_new_project_run`, `test_terminal_step_count_does_not_follow_live_events`; initial red must reproduce a concrete UI gap, not assume current one-click code absent.
- [ ] 2: unit web red command `pnpm test -- src/lib/__tests__/max-adaptation-one-click.test.tsx src/lib/__tests__/max-adaptation.test.tsx src/lib/__tests__/prompt-stream-adaptation.test.tsx`.
- [ ] 3: minimal continuation only after durable cancel confirmation and matching current project/operation; storage failure leaves actionable state; 409/503 uses safe reason/retry timing; uncertain activation shows recovery, never «данные не изменены» unless pre-effects evidence proves it.
- [ ] 4: UI labels distinguish current working version, candidate, proof, activation, recovery; wording hides internal implementation from normal user, support code available compactly. `completed` shown only after server terminal evidence; no progress event can overwrite terminal step count.
- [ ] 5: negative cases double click/two tabs/reload/stale project/expired attachment/localStorage denied/controller down/cancel race; assert at most one generation and no lost durable intent. Existing stage budgets/work plan remain; do not repeat already delivered `90b58990`/`6c6357bd` implementation.
- [ ] 6: targeted green + `pnpm typecheck`/build; manual synthetic UI check desktop/mobile with no overflow and clear retry/recovery text; API deadline tests only if changed.
- [ ] 7: perform D, commit `fix(web): keep adaptive restoration progress continuous and durable`.

**Observability/evidence:** UI screenshots of synthetic project and request-count assertions; P2 is not a substitute for P0 correctness. If no defect remains in current code, add only necessary regression and record no product change.

## 7. Redaction, retention и cleanup contract

| Класс | Разрешено хранить | Запрещено хранить | Срок / cleanup |
|---|---|---|---|
| Public release evidence | SHA, image digest, safe check ID/code, timestamps, aggregate counters | env, cookies, signed launch, provider tokens, DB URL/password, arbitrary HTTP body | Sanitized CI artifact 14 дней; summary manifest/digests 90 дней в private ops archive |
| Fixture evidence | checked-in synthetic values, generated opaque QA IDs, row/value HMAC, schema fingerprint | real business rows/dump, unredacted application logs | Sanitized witness 14 дней; HMAC key до verify, затем удалить |
| Runtime resources | manifest-owned project/DB/volumes/container labels | чужие проекты, shared DB/container deletion | Happy path immediate cleanup; failed terminal fixture TTL 24h; unresolved admitted journals retain until recovery terminal |
| Local/private diagnostics | bounded redacted relevant stack/error code, allowlist path manifest | full `docker inspect` env, full nginx config, prompt/transcript, secret-bearing git diff | Private 0700 dirs/0600 files на Linux; expire 24h после triage; Windows filesystem ACL, не фиктивный POSIX chmod |
| Production preservation | local preservation commit для документов, private artifact archive с hashes/modes | публикация env histories/backup contents в Git/CI | Owner-controlled operational retention; QA sweeper его не трогает |

До любой SQL business query runner обязан доказать synthetic scope. Redaction делается **до** stdout/artifact upload, не постфактум. В `test_restoration_release_manifest.py` ввести `test_bundle_rejects_secret_canaries`: fixture tokens `qa-token-do-not-export`, `postgresql://secret`, `Cookie=`, `initData=` не должны присутствовать в serialized bundle. Если redaction не прошла, upload только safe failure metadata; private files quarantine, не публиковать.

Cleanup не откатывает бизнес-БД. Он удаляет одноразовый QA project/его ресурсы через owner path после terminal; при crash/resume сверяет manifest и controller labels. Список удаления явный, каждый path resolved, parent внутри QA root. Windows: только native PowerShell с `-LiteralPath`, без передачи путей между shell. Продуктовые durable receipts сохраняются в объёме operational policy; unresolved effects нельзя удалять по TTL.

## 8. Release gates

| Gate | Условие PASS | Blocker |
|---|---|---|
| R0 Freshness | чистые проверенные checkout/upstream/production source, exact release map | dirty/diverged/unknown identity, unresolved T14 |
| R1 Platform/canary | service + dependencies healthy; external health 200/webhook 401; current-SHA smoke PASS | 502, stale expectations, missing canary |
| R2 Existing baseline | exact-commit full CI и targeted regressions PASS; environment gaps явно отделены | unexplained failure/skip required test |
| R3 Protected adaptive | one explicit synthetic run completed; source/candidate/proof/activation bindings, SQL witness, signed A/B, CRUD/delete/reload/restart PASS | missing checkpoint; earlier v8 substituted for new chain |
| R4 Faults | все 5 points на exact release; forward recovery, one active version/terminal settlement, no old writers after effects | only mocked unit pass; missing real process restart |
| R5 Automatic | единый профиль empty+compatible, zero generation/AI/settlement, first write preserved | hidden adaptive fallback, not_measured treated empty |
| R6 Semantics | supported behavioral cases PASS; unsupported cases fail-closed с actionable reason | route parity used as semantic proof |
| R7 Operations | dashboard/alerts, safe retry, durable cleanup, redacted complete bundle | leaked secret, stuck admission hidden, cleanup false success |
| R8 Delivery | commit + origin/main push + exact production deploy + health evidence | работа оставлена только локально или CI другой revision |

Release result `READY` только при R0–R8. `PASS_WITH_ENV_LIMITATION` можно употреблять для локального unit этапа, но итоговый release gate — FAIL/BLOCKED до обязательного Linux real-runtime evidence. P2 UX может поставляться после R0–R8, если он не блокирует выполнение сценария и его остаток явно записан.

## 9. Stop и recovery/rollback rules

1. Fetch/server comparison не завершены, неожиданный remote drift, dirty application source, чужая активная операция или непроверенная DB identity: stop перед mutation; сохранить evidence, не лечить очисткой.
2. Canary 502 неизвестного слоя: только T01 diagnosis. Не менять expected 401 на 502, не отключать smoke, не подменять URL произвольным здоровым проектом.
3. Witness потерял строку/hidden value, B получил чужие данные, unexpected network side-effect, same IDs bound к другому DB: stop release, заморозить только QA-проект, сохранить digests, исправить конкретный механизм; никакого реального business replay.
4. До effects admission можно штатно cancel и сохранить текущий code/data. После admitted или indeterminate response — forward recovery того же intent; запрет old-source writers и новой конкурирующей restoration до reconciliation.
5. Если controller недоступен, timeout — не доказательство отсутствия эффекта. Не сбрасывать state, не освобождать fence и не создавать новый idempotency key для обхода pending operation.
6. Платформенный rollback отличается от восстановления клиентского кода: разрешён только ранее проверенный image/revision, совместимый с **текущей** Alembic schema и durable journals. Не `alembic downgrade`, не import старого DB dump. Если старый release не понимает activation journal, forward-fix безопаснее; admission остаётся закрытым до решения.
7. Смена runtime images требует снова нулевых admission counters и worker heartbeats после старта. Health без release identity не открывает gate. Dirty docs/artifacts сохраняются по T14, не принудительно удаляются ради отката.
8. Failure настоящего AI run: один diagnosed retry после изменения, с новым operation для terminal failure и отдельной причиной. Не повторять платную адаптацию в цикле и не объявлять исправлением просто увеличенный deadline.
9. Secret in artifact: прекратить upload, заменить sanitized artifact, private quarantine, operational incident по фактически затронутому секрету; не копировать его в отчёт.

## 10. Evidence bundle manifest

Корень создаётся как `Path(QA_ROOT) / str(run_id)` с private permissions; `manifest.json` содержит только safe metadata. `contracts.py` определяет проверяемую структуру, а runner заполняет её фактическими значениями:

```python
class ArtifactEntry(BaseModel):
    path: str
    sha256: str
    bytes: int
    producer_sha: str
    check_ids: list[str]

class ReleaseEvidence(BaseModel):
    format: Literal[1] = 1
    audit_base_sha: Literal['0676e0be967b718b3c064c9ebbbb9c769fd36fdf']
    release_sha: str
    identities: ReleaseIdentity
    checks: list[CheckResult]
    fault_points: list[str]
    artifacts: list[ArtifactEntry]
    redaction_passed: bool
    cleanup_status: Literal['PASS', 'FAIL', 'BLOCKED']
    remaining_owned_resources: int

required_faults = {
    'api_after_proof_before_commit', 'worker_after_durable_offer',
    'orchestrator_during_activation', 'lost_accepted_apply_response',
    'duplicate_callback',
}
assert re.fullmatch(r'[0-9a-f]{40}', bundle.release_sha)
assert set(bundle.fault_points) == required_faults
assert all(value == bundle.release_sha for value in bundle.identities.model_dump().values())
assert bundle.checks and all(check.status == 'PASS' for check in bundle.checks)
assert bundle.artifacts and bundle.redaction_passed
assert bundle.cleanup_status == 'PASS' and bundle.remaining_owned_resources == 0
```

Это минимальные assertions, не вся gate implementation: validator также требует полный набор обязательных check IDs R0–R8, одинаковые release bindings, существование и hashes артефактов, проверенные controller/SQL signatures, времена наблюдения внутри прогона. Пустые checks, synthetic вручную выставленный PASS и неполная fault matrix отвергаются. Исторические bounded PASS хранятся отдельно от текущих обязательных checks.

Обязательные artifacts и связи:

- `freshness.json`: local/remote/server branch/status/revision, ahead/behind, observed time, safe server dirtiness manifest.
- `release.json`: commit/push evidence, CI run SHA/result, service image IDs и actual source identities before/after.
- `canary-diagnosis.json`, `smoke.json`: root-cause layer, safe hop observations, exact current workflow run URL.
- `qa-scope.json`: synthetic manifest/resource ownership/fixture hash/DB identity/TTL, без credentials.
- `adaptive.json`: selected version, base snapshot, operation/run/activation IDs, state/phase/deadline stages, source/candidate/proof/receipt digests.
- `witness-*.json`: все T04 checkpoints, stable row IDs/HMACs, permitted CRUD deltas и foreign/dependent invariants.
- `owners.json`, `crud.json`, `restart.json`, `receipts.json`: независимые assertions, safe HTTP codes, lineage and terminal quota/charge deltas.
- `faults/<point>.json`: пять точек, durable checkpoint, новый process identity, terminal invariants и no-old-writers evidence.
- `automatic.json`: exactly empty+compatible одного release, zero AI/run/settlement; `semantics.json`: matrix expectation/actual.
- `observability.json`: alert activation/resolution и safe retry; `cleanup.json`: per-resource outcome, preserved unresolved state если есть.
- `index.json`: relative artifact path, SHA-256, byte size, producer exact SHA и check IDs; symlink/outside-root path rejected. `verify-bundle` повторно считает hashes и semantic links, а не доверяет только `status=PASS`.

## 11. Точные волны исполнения и критический путь

| Волна | Задачи | Начальный вход | Завершение / delivery |
|---|---|---|---|
| W0 | Observe LIVE-16; T01 read-only, preflight | stopping point §0.1 + clean audit checkout + production evidence | terminal/reconcile исход текущей операции; воспроизведённый canary hop/root cause; без speculative writes |
| W1 | T14 hygiene и T12 harness; T01 minimal fix | named preservation manifest, isolated Linux resources | разрешённая чистая поставка; real Docker runner; canary 200/401 |
| W2 | T02; T17–T19 new P0 defects; T03 fixture + T04 witness; T16 NEW-25 | exact pushed/deployed SHA, canary fixed | signed business route is mandatory; starter preserved; failed generation has no hidden pre-admission DB effects; deterministic fixture/witness; composer respects no-seed |
| W3 | T05, T06, T07, T08 | owned fixture, signed sessions, independent SQL | полный ready-to-run acceptance harness, receipt/settlement DB tests |
| W4 | T09, T10, T11; T13 | working harness, exact integrated release | five faults, automatic profile, semantics, alerts/retry accepted |
| W5 | final exact-release CI/deploy/T02, one T03 live adaptive run, T04–T08 checkpoints | frozen release candidate; all harness tests PASS | R0–R8 bundle + cleanup; completed fresh protected chain |
| W6 | T15 и точечные остатки | product readiness evidence | ordinary D; rerun только затронутые checks, полный gate если изменена protected chain |

Оценка дана в блоках сосредоточенной инженерной работы, без календарных обещаний. Checkbox — одно действие на 2–5 минут; длительные CI/build/model ожидания измеряются отдельно.

- T01 diagnosis 6–10 блоков; fix зависит от доказанного слоя, 4–12 дополнительных блоков. Нельзя оценить неизвестную причину как готовый исправленный nginx.
- T12/T14 prerequisite 12–20 блоков вместе при готовом Linux runner и доступном разрешении preservation. Внешние доступы/согласование именованных файлов — отдельное ожидание, не инженерный throughput.
- T03/T04 18–26 блоков; T05–T08 24–36 блоков. Здесь общие contracts и fixture serial dependencies не дают распараллелить всё по слоям.
- T09 12–20 блоков плюс real process runs; T10/T11 12–20 блоков; T13 8–12 блоков. Эти проверки частично независимы после стабильного harness.
- W5 8–12 управляющих блоков плюс один real adaptive run/CI/deploy и measured runtime QA. Edit/repair/proof ceilings не складывать в обещанную длительность успешного прогона.

Критический путь после нового live evidence: `observe LIVE-16 → T01 root cause → T14 clean delivery → T17/T18/T19 source–schema/proof fixes → T02 release smoke → T03/T04 fixture+witness → T05/T06/T07/T08 linked acceptance → T09 required recovery → W5 exact-release run → bundle validation`. Для новых T17–T19 добавить ориентировочно 18–30 focused блоков плюс real-DB reproduction; это оценка по зависимостям, а не обещание календарного срока. T12 должен завершиться до real Docker ветки; T10/T11/T13 можно готовить рядом независимыми read/test задачами, но ни один из R5–R7 нельзя обойти. Изменение release после W5 инвалидирует identity binding затронутых результатов: повторять нужную цепочку на новом SHA, а не все старые ручные аудиты.

## 12. Self-review checklist планировщика и исполнителя

- [x] Сохранён положительный live baseline 19–20.09; fresh gap относится к изменённому isolated proof/activation path, а не ко всем откатам вообще.
- [x] Application failures, environment limitations и unproven properties разделены; root cause 502 обозначен UNKNOWN до T01.
- [x] Stage budgets/work plan/provenance/lock fixes не предлагаются повторно как отсутствующий код.
- [x] Все existing file references проверяются path scan; новые файлы отмечены Create. Product edits допускаются только при конкретном failing regression.
- [x] P0 охватывает synthetic completed adaptation, independent SQL witness, signed two-owner, CRUD/delete/reload, app/Cell restart и receipt/settlement отдельно.
- [x] Пять faults имеют точный boundary, тест, допустимый rehearsal и forward-only invariant; существующие unit recovery tests сохранены.
- [x] Empty+compatible — один deterministic профиль exact release без generation/AI/settlement; adaptive только explicit.
- [x] Реальные business rows/dump запрещены; schema-only и synthetic-only candidate copy разведены; redaction/retention/cleanup описаны.
- [x] Owner header не подменяет подписанную MAX identity; paid usage rows не подменяют terminal settlement count.
- [x] Windows/Node limitations не замалчиваются, required Linux E2E не skip; CI unit DB не назван real Docker acceptance.
- [x] Включены telemetry/alerts/operator retry, clean production checkout, current-SHA smoke и mandatory adaptive gate.
- [x] Каждая поставка, включая этот docs-only документ, включает tests → commit → push origin/main → full Compose deploy → service/HTTP/revision evidence; продуктовые remediation-задачи не выдаются за выполненные.
- [x] Нет обещания production-ready до R0–R8, нет old-code rollback после возможных data effects, нет blanket cleanup чужой работы.
- [x] Промежуточный live-срез содержит exact project/owner/run/restoration IDs, отдельные PASS/FAIL/BLOCKED, недоказанные свойства и stopping point. NEW-26/27/28 имеют отдельные P0 tasks; LIVE-16 стала PASS только после terminal receipt, 46-file exact match, DB 0/0 и fresh signed GET 200.
- [ ] Исполнитель перед стартом обновляет preflight и отмечает actual commits/check results; planner checkboxes выше подтверждают полноту документа, не выполнение задач.

Итоговый handoff этого промежуточного среза: сверить завершённую LIVE-16 по §0.1, затем продолжить с synthetic rows/compatible/adaptive и независимыми свидетелями; повторять empty QA с нуля не нужно. Remediation: T01 read-only canary diagnosis и новые P0 T17–T19. Параллельно допустим T12 Linux harness в отдельном clean worktree. Документ подлежит commit/push сейчас и обязательному deploy после свежих admission gates; product remediation и полная live acceptance ещё не завершены.
