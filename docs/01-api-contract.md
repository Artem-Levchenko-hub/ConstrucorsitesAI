# 01. API Contract — единая правда для A/B/C

Любое изменение этого файла обязано сопровождаться записью в `~/.claude/coordination/omnia-mvp/inbox/` и согласованием со всеми тремя агентами.

## Базовые соглашения

- **Префиксы:** все REST endpoints публичного API под `/api/*`. Public preview — `/p/*`. WebSocket — `/api/ws/*`.
- **Формат:** JSON, UTF-8, snake_case в payload.
- **Время:** ISO 8601 UTC (`2026-05-04T10:30:00Z`).
- **Идентификаторы:** UUID v4, в JSON как строка.
- **Auth:** JWT в `httpOnly` Secure cookie `omnia_session`. WebSocket — токен из cookie или `?token=` query.
- **Ошибки:** `{ "error": { "code": "string_const", "message": "human", "details": { ... } } }` + соответствующий HTTP-статус.

## REST endpoints (apps/web → apps/api на :8000)

### Auth

| Метод | Path | Тело | Ответ | Статус |
|---|---|---|---|---|
| `POST` | `/api/auth/register` | `{email, password}` | `User` + Set-Cookie | 201 |
| `POST` | `/api/auth/login` | `{email, password}` | `User` + Set-Cookie | 200 |
| `POST` | `/api/auth/logout` | — | — | 204 |
| `GET` | `/api/auth/me` | — | `User` | 200 / 401 |

**Валидация регистрации:** email формат, password ≥ 8 символов и хотя бы 1 цифра. Хэш — bcrypt (12 rounds).

#### Вход через VK ID и Яндекс ID (`routers/auth_oauth.py`, миграция `0070`)

Аккаунт остаётся «email + пароль»: от провайдера платформа берёт только его идентификатор
пользователя и email (таблица `user_identities`), токены провайдера не сохраняются.
Рукопожатие серверное: `state` и PKCE-verifier лежат в `oauth_login_states` с TTL
(state — 10 минут, билет подтверждения — 15 минут), браузер получает только ссылку.

| Метод | Path | Тело / query | Ответ | Статус |
|---|---|---|---|---|
| `GET` | `/api/auth/oauth/providers` | — | `{providers: [{provider: "vk"\|"yandex", label}], legal_document_version}` — только настроенные | 200 |
| `GET` | `/api/auth/oauth/:provider/start` | `?next=/max/…` (same-origin путь, иначе `/max`) | `{authorization_url}` | 200 / 404 `oauth_provider_unavailable` |
| `GET` | `/api/auth/oauth/:provider/callback` | `?code&state` (+ `device_id` у VK; `error` при отказе) | 303-редирект в web (см. ниже) | 303 |
| `GET` | `/api/auth/oauth/pending` | `?ticket=` | `{provider, label, email, next, legal_document_version}` | 200 / 400 `oauth_ticket_invalid` |
| `POST` | `/api/auth/oauth/complete` | `{ticket, terms_accepted, privacy_accepted, personal_data_accepted, marketing_accepted?, document_version}` | `User` + Set-Cookie | 201 / 422 `legal_acceptance_required` / 409 `legal_version_outdated` / 400 `oauth_ticket_invalid` / 403 `account_unavailable` |

Куда уходит callback (`WEB_BASE_URL` + путь):

- аккаунт найден по связке провайдера или по его email → `Set-Cookie: omnia_session` (та же
  сессия, что при входе по паролю) + `303 → <next>` (по умолчанию `/max`); найденный по email
  аккаунт получает связку и `email_verified_at`;
- аккаунта нет → `303 → /oauth/complete?ticket=<одноразовый>`; аккаунт **не** создаётся до
  `POST /complete` с теми же согласиями, что и у `register` (`product=max`); новый аккаунт —
  без пароля, с `email_verified_at`, личным платёжным счётом, кошельком и Free-подпиской;
- отказ/сбой → `303 → /login?oauth_error=<код>[&next=…]`, коды: `oauth_cancelled`,
  `oauth_state_invalid`, `oauth_exchange_failed`, `oauth_email_required`,
  `oauth_provider_unavailable`, `account_unavailable`.

VK ID — OAuth 2.1 с PKCE (`code_challenge_method=s256`, scope `email`), обмен кода без client
secret, но с `device_id` из callback-а. Яндекс ID — OAuth 2.0 с client id + secret, scope
`login:email`. Redirect URI провайдера: `<OAUTH_LOGIN_REDIRECT_BASE_URL|WEB_BASE_URL>/api/auth/oauth/<vk|yandex>/callback`
(runbook — `docs/plans/2026-09-23-oauth-login.md`). Rate limit — как у `login`/`register`.
Выгрузка аккаунта (`GET /api/account/export`) отдаёт связки в поле `identities`.

`User.role` принимает `user | admin`. Роль хранится в PostgreSQL; `ADMIN_EMAILS`
используется только как bootstrap-доступ. Все admin endpoints дополнительно
проверяют активную сессию и effective admin role.

### Admin

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `GET` | `/api/admin/access` | — | `{is_admin, role, email}` |
| `GET` | `/api/admin/users` | `?query=&limit=` | `AdminUser[]` |
| `PATCH` | `/api/admin/users/:id` | `{role?, email_verified?, status?, note?}` | `AdminUser` |
| `GET` | `/api/admin/audit` | `?limit=` | `AdminAuditEvent[]` |

Изменение роли, статуса и верификации записывается в `admin_audit_events` с
инициатором, целевым аккаунтом и состоянием до/после. Самоблокировка и снятие
собственной persisted admin-роли запрещены.

### Projects

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects` | `{name, template?: "max_miniapp"}` — с 19.09.2026 только MAX и только для вошедшего пользователя (гость — 403 `max_registration_required`, другой шаблон — 422) | `Project` |
| `GET` | `/api/projects` | — | `Project[]` (только свои) |
| `GET` | `/api/projects/:id` | — | `Project` |
| `DELETE` | `/api/projects/:id` | — | 204 (orchestrator destroy для fullstack) |

`kind` (V2): `static` → V1 шаблоны (`blank/landing/portfolio/blog`). `fullstack` → V2 шаблоны (`nextjs-postgres-drizzle`, далее — `nextjs-supabase`, `fastapi-postgres`, `nextjs-resend`, `telegram-bot-python` и т.д.). Дефолт — `static` для backward-compatibility до полного V2 launch.

### Промпт и снапшоты

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects/:id/prompt` | `{prompt: string, idempotency_key?: string, model_id?: string, selected_elements?: SelectedElement[]}` | `{run_id, message_id, snapshot_id?, mode, ...}` (snapshot_id появится позже через WS) |
| `POST` | `/api/projects/:id/generation/cancel` | — | `GenerationRun` со статусом `cancel_requested` (202) |
| `GET` | `/api/projects/:id/generation` | — | Последний durable `GenerationRun` или `null`; используется для восстановления реального статуса после перезагрузки |
| `GET` | `/api/projects/:id/snapshots` | — | `Snapshot[]` (DESC по `created_at`) |
| `GET` | `/api/projects/:id/snapshots/:sid` | — | `Snapshot & { files: { [path]: string } }` |
| `POST` | `/api/projects/:id/rollback` | `{snapshot_id}` | `Snapshot` (новый — результат отката) |
| `GET` | `/api/projects/:id/messages` | `?limit=50&before=<msg_id>` | `Message[]` |

`idempotency_key` — стабильный UUID одного пользовательского submit. Повтор
`POST /prompt` с тем же ключом и тем же текстом возвращает исходный ответ и не
создаёт вторую пару сообщений/генерацию. Переиспользование ключа с другим текстом
даёт `409 idempotency_conflict`. На проект допускается ровно один активный
`GenerationRun`; параллельный запрос с другим ключом получает `409 generation_active`
(в `details` — `active_run_id`, `active_message_id`, `active_status`).

С 19.09.2026 отказ `409` на `POST /prompt` всегда называет причину — клиент не
должен её угадывать: `generation_active` (идёт сборка; только этот код разрешает
показывать «Генерация уже запущена»), `restoration_active` (идёт восстановление
версии, в `details.restoration_id` — операция; тот же код получают предпросмотр
владельца, публикация, настройки MAX и удаление проекта), `source_changed` (данные
приложения MAX изменились с момента открытия формы), `idempotency_conflict`.

Stop — серверная операция: `/generation/cancel` записывает durable-статус,
передаёт сигнал через Redis и отменяет реальную coroutine генерации, а не только
закрывает WebSocket в браузере. После рестарта API незавершённые process-local
запуски переводятся в `failed`, чтобы проект не оставался навечно заблокирован.

### Тарифы, подписка и кошелёк

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `GET` | `/api/billing/plans` | — | Активные версии тарифов `Free`, `Pro`, `Business` |
| `GET` | `/api/billing/subscription` | — | Текущая подписка платёжного аккаунта вместе с зафиксированной версией тарифа |
| `PATCH` | `/api/billing/subscription` | `{action: "cancel" \| "restore", consent_version?}` | Отмена в конце периода или восстановление автопродления с актуальным согласием |
| `GET` | `/api/billing/usage` | query `from?`, `to?` (ISO-даты) | Журнал расхода аккаунта за период: `period` (по умолчанию — оплаченный период подписки, на Free — текущий календарный месяц UTC), `plan`, `generations` (сборки из `generation_runs` + их расход по `usage`), `app_ai_answers` (ответы ИИ посетителям приложений — строки `usage` со `stage = runtime_ai`), `other_ai`, `publications` (журнал `billing_usage_events`), `free_generations` (`limit/used/left`), `wallet` (`balance_rub`, `debited_rub`, `credited_rub` за период), `entitlements[]` — каждый лимит тарифа рядом с текущим использованием (`limit: null` = без ограничений, `exceeded`) |
| `GET` | `/api/wallet` | — | `{balance_rub, recent_charges}`; каждая операция содержит `entry_type`, `balance_after_rub`, `external_ref` |
| `POST` | `/api/wallet/topup` | `{amount_rub}` | `{balance_rub}`; тестовый маршрут закрыт по умолчанию |
| `GET` | `/api/payments` | — | Последние платежи текущего платёжного аккаунта |
| `POST` | `/api/payments` | `{package_code, idempotency_key}` | Разовое пополнение через ЮKassa; недоступно без реквизитов магазина |
| `POST` | `/api/payments/subscription` | `{plan_code: "pro" \| "business", idempotency_key, auto_renew, consent_version?}` | Создаёт pending-подписку и redirect-платёж первой покупки; способ оплаты сохраняется только при явном согласии |

Регистрация создаёт личный платёжный аккаунт и одну активную Free-подписку
(с миграции `0069` аккаунт всегда личный: бизнес-контура нет).
Каталог тарифов версионируется: существующая подписка продолжает ссылаться на
купленную ревизию, а изменение цены или лимитов создаёт новую строку тарифа.
Действующий Free — версия 2 (миграция `0070`, модель владельца от 17.09.2026):
число приложений и публикаций не ограничено, интеграции включены, постоянно
работающих приложений нет (Free-приложение засыпает при простое).

**Лимиты тарифа проверяются на сервере** (`services/entitlements.py`), а не
только показываются: `POST /api/projects` считает приложения против
`max_projects`; `POST /api/projects/:id/deploy` — опубликованные приложения
против `static_publish_slots` (слот занимает каждое существующее приложение,
которое хоть раз отправлялось в публичный рантайм; повторная публикация того
же приложения слот не тратит; удаление приложения слот освобождает);
подключение интеграций (`PUT/POST …/app-integrations/…`, OAuth-старт, пакет)
— флаг `integrations`; keep-alive рантайма — `always_on_slots` (как раньше).
Значение `null` у числового лимита = без ограничений. Отказ — `402` с кодом
`entitlement_exceeded` (числовой лимит исчерпан) или
`subscription_entitlement_required` (возможность не входит в тариф); в
`details` — `{entitlement, limit, used, plan_code, plan_version}`. Выключатель
на случай инцидента — `ENFORCE_PLAN_ENTITLEMENTS=false` (отказы только
логируются, отчёт `/usage` при этом показывает `exceeded`).
Первая покупка создаёт `pending_payment`, а подтверждённый ответ ЮKassa одной
транзакцией завершает прежнюю подписку, активирует купленную версию на месяц и
один раз начисляет включённый кредит. Повтор webhook безопасен. Автопродление
включается только отдельным флагом и актуальной версией согласия. Worker
повторяет неуспешное списание через 12 часов, сохраняет платные права на
трёхдневный grace-период и затем создаёт Free-подписку без удаления проектов.

### Models (для селектора в UI)

| Метод | Path | Ответ |
|---|---|---|
| `GET` | `/api/models` | `Model[]` — список с ценами в ₽/1k токенов |

### Public preview (без auth)

> **19.09.2026 — вынесено.** Адреса `/p/:slug`, `/p/:slug/*`, `/p/:slug/lead`,
> `/p/:slug/remix`, а также `POST /api/projects/import`, `/api/projects/:id/claim`,
> `/api/projects/:id/fork` и `GET /api/projects/:id/leads` принадлежали конструктору
> сайтов и живут в соседнем проекте `omnia-sitebuilder`
> (`docs/plans/2026-09-19-max-only-separation.md`). `POST /api/projects` создаёт только
> `max_miniapp` и только для вошедшего пользователя; другой шаблон — 422. Остался
> `GET /api/kit/:file`: из него черновые предпросмотры ячеек берут `omnia-inspector.js`.

Для `kind=fullstack` preview работает иначе: web iframe грузит `https://<slug>.preview.omniadevelop.ru` напрямую (apps/api в этом не участвует, см. секцию V2).

### V2: Runtime + Deploy (Phase A, доступно только для `kind=fullstack`)

apps/api тут — тонкий прокси на orchestrator. Слой авторизации (JWT cookie, ownership check) — в apps/api, бизнес-логика (Docker, postgres-schema, nginx) — в orchestrator.

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects/:id/runtime/start` | — | `RuntimeStatus` (после wake) |
| `POST` | `/api/projects/:id/runtime/stop` | `{pause?: bool}` | `RuntimeStatus` |
| `GET` | `/api/projects/:id/runtime` | — | `RuntimeStatus` |
| `POST` | `/api/projects/:id/deploy` | `{commit_sha?: string}` | `DeployStatus` (асинхронный, прогресс — через WS) |
| `GET` | `/api/projects/:id/deploy` | — | `DeployStatus` последнего деплоя |

> **DeployStatus, format_version 2 (P01, 18.09.2026, аддитивно):** к прежним полям добавлены `format_version` (1 — ответ старого контроллера, ничего не выдумывать), `stage` (текущая подстадия: `preflight`, `source_wake`, `source_schema`, `capture_rootfs`, `capture_volumes`, `verify_artifacts`, `resume_source`, `prepare_target`, `seed_data`, `activate`, `start_app`, `verify_runtime`, `tls`, `observe`), `stage_started_at`, `heartbeat_at` (последнее доказательство жизни операции), `progress` (`{bytes_done, bytes_total|null, files_done|null}` — неизвестный объём остаётся `null`, процент по времени не рисуется), `stages[]` (история подстадий с `elapsed_ms`), `metrics` (`prepare_ms`, `activate_ms`, `total_ms` и `<stage>_ms`), `error_stage` и `reason_code` (фиксированный код без сырого текста Docker/SQL). Старые `phase` и `logs` не меняются.

`commit_sha` опционален: по умолчанию — текущий HEAD проекта. Использование одного коммита — для rollback prod без пересборки.

### V2 WebSocket-события (поверх V1)

```json
{ "type": "runtime.started",  "data": { "project_id": "uuid", "dev_url": "https://...", "state": "running" } }
{ "type": "runtime.stopped",  "data": { "project_id": "uuid", "state": "paused"|"stopped" } }
{ "type": "runtime.failed",   "data": { "project_id": "uuid", "error": "string" } }
{ "type": "deploy.progress",  "data": { "project_id": "uuid", "stage": "building"|"pushing"|"running"|"healthy"|"failed", "log_tail": "..." } }
{ "type": "deploy.complete",  "data": { "project_id": "uuid", "prod_url": "https://...", "image_tag": "string" } }
```

### V2 Internal API (apps/api ↔ orchestrator на :8003)

Не доступно публично. Auth: header `X-Internal-Token` (shared secret). Полный контракт — `apps/orchestrator/src/yleum_orchestrator/schemas/runtime.py`. Краткий список:

| Метод | Path | Назначение |
|---|---|---|
| `GET`  | `/health` | docker + postgres probe |
| `POST` | `/internal/projects/provision` | clone template + container + nginx |
| `POST` | `/internal/projects/wake` | start/unpause |
| `POST` | `/internal/projects/stop` | pause или stop по tier |
| `POST` | `/internal/projects/hot-reload` | copy AI-сгенерированных файлов в running container |
| `POST` | `/internal/projects/deploy` | build из живого контейнера → run prod → nginx → health (async) |
| `GET`  | `/internal/projects/:id/deploy` | состояние последнего деплоя (phase/prod_url/image_tag/error) |
| `GET`  | `/internal/projects/:id/status` | состояние + URLs |
| `POST` | `/internal/projects/:id/destroy` | полная очистка |

> **[D, 2026-05-22] Runtime/Deploy реализованы (был scaffold `501`).** Изменения internal-контракта — backward-compatible:
> - `DeployRequest.commit_sha` теперь **optional** (деплоим живое состояние контейнера; git-истории в runtime нет).
> - Новый `GET /internal/projects/:id/deploy` (deploy-state). **Прошу B:** проксировать в публичный `GET /api/projects/:id/deploy` (сейчас отдаёт placeholder).
> - `/stop`, `/status`: query-param `slug` теперь **optional** — контейнер резолвится по label `omnia.project_id`. Это фикс бага «пауза не останавливает» (slug не слался → 422).
> - **Interim URL-схема:** dev `https://<slug>-dev.170-168-72-200.sslip.io`, prod `https://<slug>.170-168-72-200.sslip.io` (sslip.io — ноль DNS у регистратора; HTTPS per-host certbot). Переключим на `*.preview/app.omniadevelop.ru` после wildcard DNS. Конфиг — `runtime_host_suffix`.
> - Раскатка — рестарт процесса `omnia-orchestrator` на VPS, БЕЗ пересборки api/web. Детали: `~/.claude/coordination/omnia-mvp/inbox/2026-05-22-agent-d-deploy-runtime.md`.

### V3: Onboarding + Multi-stack + Linked-deploy

> **Полный design:** `docs/10-v3-multistack-pivot.md`. Разделение работ — `agents/V3-CHAT-{1,2,3}-*.md`.
> Все эндпоинты ниже **additive, backward-compatible** — V1/V2 потребители не ломаются.

**Onboarding (Chat-2 owner):**

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects/onboarding/start` | `{brief: string, linked_repo_id?: string}` | `OnboardingSession` (state=`asking-Q`, первый вопрос в `current_question`) |
| `POST` | `/api/projects/onboarding/:sid/answer` | `{answer: string}` или `{skip: true}` | `OnboardingSession` (state переходит дальше, см. state-diagram в spec) |
| `POST` | `/api/projects/onboarding/:sid/confirm-stack` | `{stack_id: string}` (из top-3 либо override из каталога) | `OnboardingSession` (state=`recommending-preset`) |
| `POST` | `/api/projects/onboarding/:sid/confirm-preset` | `{preset_id: string}` (либо `{auto: true}`) | `OnboardingSession` (state=`complete`) + `project_id` |
| `GET` | `/api/projects/onboarding/:sid` | — | `OnboardingSession` (восстановить state в UI после рефреша) |

**Stack catalog + recommender (Chat-2 owner, dispatches Chat-3 LLM Gateway):**

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `GET` | `/api/stacks` | — | `StackTemplate[]` (весь каталог из `stack_templates` table) |
| `POST` | `/api/projects/stack/recommend` | `{brief: string, answers?: {question: string, answer: string}[]}` | `{recommendations: StackRecommendation[]}` (top-3) |

**Preset catalog (Chat-2 owner, read freeze):**

| Метод | Path | Query | Ответ |
|---|---|---|---|
| `GET` | `/api/presets` | `?category=palette\|font_pair\|pattern\|component\|framework_docs` | `UiKitEntry[]` |
| `GET` | `/api/presets/:slug/preview` | — | `UiKitEntry & {render_html: string}` (HTML-превью пресета для карусели) |

**Linked-repo + GitHub OAuth (Chat-2 owner):**

| Метод | Path | Тело/Query | Ответ |
|---|---|---|---|
| `GET` | `/api/auth/github/init` | `?redirect=<path>` | `302` → `github.com/login/oauth/authorize?...` |
| `GET` | `/api/auth/github/callback` | `?code=...&state=...` | `302` → `redirect` query (с linked_repo_id в session-cookie) |
| `POST` | `/api/projects/connect-repo` | `{repo_full_name: string, branch: string, project_id?: string}` | `LinkedRepo` + (если project_id) обновлённый `Project.linked_repo_id` |
| `GET` | `/api/repos/list` | — | `{name, full_name, default_branch, private}[]` (GitHub list через access_token) |
| `GET` | `/api/projects/:id/repo` | — | `LinkedRepo \| null` |
| `DELETE` | `/api/projects/:id/repo` | — | 204 (отвязать, project продолжит жить с native deploy) |

**Deploy-link (V3 — push в GitHub юзера, Chat-2 owner, Chat-3 не участвует):**

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects/:id/deploy-link` | `{commit_message?: string}` | `DeployLinkStatus` (async через WS) |
| `GET` | `/api/projects/:id/deploy-link` | — | `DeployLinkStatus` (последний) |

**Native deploy (Chat-3 owner, расширение V2 под multi-stack):** существующий `POST /api/projects/:id/deploy` остаётся, теперь orchestrator выбирает Dockerfile по `project.stack_id`. Контракт не меняется.

### V3 WebSocket-события (поверх V1/V2)

```json
{ "type": "onboarding.next_question", "data": { "session_id": "uuid", "question": "...", "why": "...", "step": 1, "max_steps": 5 } }
{ "type": "onboarding.recommending_stack", "data": { "session_id": "uuid", "recommendations": [{"stack_id":"...","score":0.92,"reasoning":"..."}] } }
{ "type": "onboarding.recommending_preset", "data": { "session_id": "uuid", "preset_id": "...", "preview_html": "..." } }
{ "type": "onboarding.complete", "data": { "session_id": "uuid", "project_id": "uuid" } }
{ "type": "deploy.linked.progress", "data": { "project_id": "uuid", "stage": "cloning"|"committing"|"pushing"|"complete"|"failed", "commit_url": null|"..." } }
{ "type": "repo.connected", "data": { "linked_repo_id": "uuid", "repo_full_name": "owner/name" } }
```

## WebSocket: `/api/ws/projects/:id`

**Подключение:** `ws://localhost:8000/api/ws/projects/:id` с cookie `omnia_session` или `?token=<jwt>`.

**Server → client события:**

```json
{ "type": "snapshot.created", "data": { "snapshot": Snapshot } }
{ "type": "preview.ready",   "data": { "snapshot_id": "uuid", "preview_url": "https://.../previews/uuid.png" } }
{ "type": "llm.chunk",       "data": { "message_id": "uuid", "delta": "...текст" } }
{ "type": "llm.done",        "data": { "message_id": "uuid", "tokens_in": 1200, "tokens_out": 4500, "cost_rub": 3.75 } }
{ "type": "llm.error",       "data": { "message_id": "uuid", "error": "string" } }
{ "type": "generation.cancel_requested", "data": { "run_id": "uuid", "message_id": "uuid" } }
{ "type": "generation.cancelled", "data": { "run_id": "uuid", "message_id": "uuid" } }
{ "type": "wallet.updated",  "data": { "balance_rub": 1234.56 } }
```

**Client → server:** только `{ "type": "ping" }` для keep-alive.

## LLM Gateway internal API (apps/api → apps/llm-gateway на :8001)

**OpenAI-совместимый endpoint** (LiteLLM proxy):

```
POST http://llm-gateway:8001/v1/chat/completions
Content-Type: application/json

{
  "model": "claude-sonnet-4-6" | "claude-opus-4-7" | "claude-haiku-4-5" | "gpt-4.1" | "gpt-5-mini" | "yandexgpt-5" | "qwen-3-coder" | "gigachat-2" | "gigachat-2-pro" | "gigachat-2-max",
  "messages": [{ "role": "system" | "user" | "assistant", "content": "..." }],
  "stream": true,
  "user": "<user_id>",                  // для usage tracking
  "metadata": {                          // omnia-specific
    "project_id": "<uuid>",
    "message_id": "<uuid>"
  }
}
```

**Ответ:** SSE-стрим OpenAI-формата (`data: {"choices":[{"delta":{"content":"..."}}]}`) + финальный `data: [DONE]`. На стороне gateway добавляется учёт токенов и списание из `wallets` ДО возврата `[DONE]`.

**Дополнительно:**

- `GET :8001/v1/models` — `{ "data": [{ "id": "claude-sonnet-4-6", "price_rub_per_1k_in": 0.3, "price_rub_per_1k_out": 1.5, "context_window": 200000 }, ...] }`
- `GET :8001/health` — `{ "status": "ok" }`

### V3 LLM Gateway endpoints (Chat-3 owner; вызывает apps/api)

```
POST :8001/v1/onboarding/next_question
{
  "brief": "...",
  "qa_pairs": [{"question":"...","answer":"..."}],
  "user": "<user_id>"
}
→ { "done": false, "question": "...", "why": "..." }  // или { "done": true }
```

Под капотом — Haiku-4.5 с фиксированным prompt-шаблоном (см. `docs/10-v3-multistack-pivot.md`). Цена ~₽0.05/вызов. Записывает usage в общую `usage` таблицу с `purpose='onboarding_q'`.

```
POST :8001/v1/stack/recommend
{
  "brief": "...",
  "answers": [{"question":"...","answer":"..."}],
  "user": "<user_id>"
}
→ { "recommendations": [{"stack_id":"...","score":0.92,"reasoning":"..."}, ...3 max] }
```

Под капотом — Haiku-4.5; закрытый список stack_id отдаётся в system prompt. Цена ~₽0.10/вызов. `purpose='stack_recommend'`.

## Формат AI-ответа (как агент C просит модель отдавать файлы)

Системный промпт инструктирует модель отдавать **полные файлы** в XML-разметке:

```
<file path="index.html">
<!DOCTYPE html>
<html>...</html>
</file>

<file path="style.css">
body { ... }
</file>
```

Это парсит **агент B** в `services/file_extractor.py`. Если файла нет в ответе — оставляет старую версию. Если файл указан, но пустой — удаляет.

**Защита:** path-валидация — никаких `..`, абсолютных путей, `/etc/`, `~`. Максимум 100 файлов и 2 МБ на файл.

## TypeScript / Pydantic типы

Эталонные определения. Frontend пишет TypeScript, backend — Pydantic; они должны совпадать поле-в-поле.

```typescript
// Эти типы — единственная правда. apps/web/src/lib/api/types.ts должен совпадать.

export type User = {
  id: string;
  email: string;
  created_at: string;        // ISO 8601
  last_login_at: string | null;
};

export type Project = {
  id: string;
  owner_id: string;
  name: string;
  slug: string;              // для /p/:slug (static) или <slug>.preview.omniadevelop.ru (fullstack)
  kind: "static" | "fullstack";   // V2: режим работы
  template: string;          // "blank"|"landing"|"portfolio"|"blog" для static;
                             // "nextjs-postgres-drizzle"... для fullstack
  design_preset_id?: string; // v3.0 auto-classifier: 'editorial-trust'|'studio-showreel'|
                             // 'saas-product'|'scandi-editorial'|'festival-brutalist'|
                             // 'wellness-casual'|'boutique-reel'|'editorial-publication'.
                             // Каталог — docs/09-generated-site-presets.md. Migration 0007.
  design_preset_name?: string;  // computed человекочитаемое имя пресета для UI-badge.
  current_snapshot_id: string | null;
  // V2 fullstack-only поля (null для static):
  dev_url: string | null;    // https://<slug>.preview.omniadevelop.ru
  prod_url: string | null;   // https://<slug>.app.omniadevelop.ru после первого deploy
  runtime_state: "provisioning" | "running" | "paused" | "stopped" | "failed" | null;
  tier: "free" | "pro" | "business";
  // V3 fields (null до завершения онбординга или для legacy V1/V2-проектов):
  stack_id: string | null;              // FK stack_templates.id, e.g. "static-html" | "nextjs-postgres-drizzle" | ...
  preset_id: string | null;             // FK ui_kit_freeze.slug | legacy design_presets.id
  onboarding_session_id: string | null; // FK onboarding_sessions.id
  linked_repo_id: string | null;        // FK linked_repos.id; если null — deploy идёт на наш поддомен
  estimated_setup_cost_rub: number | null; // справочно для UI, заполняется после онбординга
  created_at: string;
  updated_at: string;
};

// V2: runtime lifecycle
export type RuntimeStatus = {
  project_id: string;
  state: "provisioning" | "running" | "paused" | "stopped" | "failed";
  dev_url: string | null;
  last_activity_at: string | null;
  cpu_pct: number | null;
  memory_mb: number | null;
  // Подписанный URL для просмотра последних 200 строк stdout/stderr.
  // Истекает через 5 минут, выдаётся orchestrator-ом.
  logs_tail_url: string | null;
};

// V2: deploy lifecycle
export type DeployStatus = {
  project_id: string;
  image_tag: string;                     // proj-<id>:<commit-sha>
  state: "building" | "pushing" | "running" | "healthy" | "failed";
  prod_url: string | null;               // выставляется на `healthy`
  deployed_at: string | null;
  error: string | null;                  // если state=failed
};

export type Snapshot = {
  id: string;
  project_id: string;
  commit_sha: string;
  prompt_text: string | null;       // null для initial и rollback
  model_id: string | null;          // какой моделью сгенерирован
  parent_id: string | null;
  preview_url: string | null;       // null пока Playwright не отрендерил
  is_rollback_target: boolean;      // если true — snapshot был использован как точка отката
  created_at: string;
};

// Select-mode: элемент, выделенный пользователем в превью, с комментарием.
// Опционально прикладывается к POST /prompt и сохраняется на user-сообщении,
// чтобы история чата перерисовывала чипы. Все поля, кроме selector, опциональны;
// длины ограничены на бэкенде (selector≤600, html≤2000, text≤300, comment≤1000,
// не более 12 элементов). Backward-compatible — старые клиенты поле не шлют.
export type SelectedElement = {
  selector: string;
  label?: string | null;
  html?: string | null;
  text?: string | null;
  comment?: string | null;
};

export type Message = {
  id: string;
  project_id: string;
  snapshot_id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  selected_elements?: SelectedElement[] | null;
  created_at: string;
};

export type Model = {
  id: string;                          // "claude-sonnet-4-6"
  display_name: string;                // "Claude Sonnet 4.6"
  provider: "anthropic" | "openai" | "yandex" | "alibaba";
  price_rub_per_1k_in: number;
  price_rub_per_1k_out: number;
  context_window: number;
  recommended_for: ("fast" | "quality" | "budget")[];
};

// ───────────────── V3 TYPES (multi-stack + onboarding + linked-repo) ─────────────────

export type StackTemplate = {
  id: string;                          // "static-html" | "nextjs-postgres-drizzle" | ...
  display_name: string;                // "Static HTML"
  description: string;                 // одно предложение что внутри
  when_to_use: string;                 // LLM-критерий
  priority: "P0" | "P1" | "P2";        // V3 launch priority
  template_dir: string;                // путь в apps/orchestrator/templates/<id>/
  supported_features: string[];        // ["ssr", "db", "auth", "realtime", ...]
  created_at: string;
};

export type StackRecommendation = {
  stack_id: string;                    // FK StackTemplate.id
  score: number;                       // 0..1
  reasoning: string;                   // одно предложение почему
};

export type OnboardingSession = {
  id: string;
  user_id: string;
  state: "asking-Q" | "recommending-stack" | "recommending-preset" | "complete" | "abandoned";
  brief: string;                       // первое описание идеи юзером
  step: number;                        // текущий вопрос (1..5)
  max_steps: number;                   // обычно 5
  current_question: string | null;     // null когда state != asking-Q
  why: string | null;                  // обоснование от Haiku — зачем спрашивает
  qa_pairs: { question: string; answer: string }[]; // история Q+A
  stack_recommendations: StackRecommendation[] | null; // заполняется при state=recommending-stack
  chosen_stack_id: string | null;      // выбор юзера на confirm-stack
  chosen_preset_id: string | null;     // выбор юзера на confirm-preset
  linked_repo_id: string | null;       // если онбординг начался с connect-repo
  project_id: string | null;           // заполняется на state=complete
  created_at: string;
  updated_at: string;
};

export type UiKitEntry = {
  slug: string;                        // "palette-editorial-trust", "font-pair-saas-modern", ...
  source: "ui-ux-pro-max" | "context7" | "manual" | "design-presets-v2-fallback";
  category: "palette" | "font_pair" | "pattern" | "component" | "framework_docs";
  name: string;                        // человекочитаемое
  payload: Record<string, unknown>;    // формат зависит от category, см. docs/10
  applicable_stacks: string[];         // ["nextjs-postgres-drizzle", ...] | [] = универсальный
  applicable_presets: string[];        // legacy preset_ids из docs/09 | []
  created_at: string;
  updated_at: string;
};

export type LinkedRepo = {
  id: string;
  user_id: string;
  provider: "github";                  // V3 только github; gitlab/bitbucket — V4+
  github_user_id: number;
  github_username: string;
  repo_full_name: string | null;       // "owner/name" — null до confirm-repo
  branch: string;                      // дефолт "omnia/deploy"
  access_token_encrypted: never;       // НИКОГДА не возвращается клиенту; только серверная колонка
  connected_at: string;
  last_push_at: string | null;
};

export type DeployLinkStatus = {
  project_id: string;
  linked_repo_id: string;
  state: "idle" | "cloning" | "committing" | "pushing" | "complete" | "failed";
  commit_url: string | null;           // GitHub commit URL после успеха
  pushed_at: string | null;
  error: string | null;
};

// ────────────────────────────────────────────────────────────────────────────────────

export type Charge = {
  id: string;
  message_id: string | null;
  amount_rub: number;                  // negative for charge, positive for topup
  description: string;                 // "Generated lending with Claude Sonnet 4.6"
  created_at: string;
};

export type ApiError = {
  error: {
    code: "validation_failed" | "unauthorized" | "forbidden" | "not_found"
        | "rate_limited" | "wallet_empty" | "model_unavailable" | "internal_error"
        // V2-добавления:
        | "container_failure" | "docker_unavailable" | "postgres_unavailable"
        | "port_exhausted" | "conflict"
        // Причина отказа 409 на POST /prompt (19.09.2026):
        | "generation_active" | "restoration_active" | "idempotency_conflict" | "source_changed"
        // Лимиты тарифа (402, details = {entitlement, limit, used, plan_code, plan_version}):
        | "entitlement_exceeded" | "subscription_entitlement_required"
        // V3-добавления:
        | "onboarding_invalid_state" | "stack_not_found" | "preset_not_found"
        | "github_oauth_failed" | "github_repo_inaccessible" | "deploy_link_failed"
        | "ui_kit_freeze_empty";
    message: string;
    details?: Record<string, unknown>;
  };
};
```

## Rate limits

| Endpoint | Лимит |
|---|---|
| `/api/auth/login` `/register` | 5/мин на IP |
| `/api/projects/:id/prompt` | 10/мин на user, 100/час |
| `/api/projects/:id/deploy` | 5/час на user (V2 — деплой ресурсоёмкий) |
| `/api/projects/:id/runtime/start` | 30/мин на user (V2 — wake может быть частым) |
| `/api/projects/onboarding/*` (V3) | 30/мин на user (включая Haiku-вызовы под капотом) |
| `/api/auth/github/*` (V3) | 10/мин на user (OAuth init/callback) |
| `/api/projects/:id/deploy-link` (V3) | 5/час на user (GitHub API quota) |
| `/api/projects/stack/recommend` (V3) | 20/мин на user |
| Остальные | 60/мин на user |

Заголовки: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

## Черновик: сохранить или отбросить несохранённые правки (25.09.2026)

Откат версии честно отказывает, пока файлы черновика отличаются от сохранённой версии
(blocker «Текущие файлы отличаются от сохранённой версии…»): восстанавливать поверх
несохранённой работы — терять её молча. Два осознанных действия владельца снимают это состояние.
Оба работают только между генерациями: активная сборка или подготовка отката → `409`.

| Метод | Путь | Ответ |
|---|---|---|
| `POST` | `/api/projects/{project_id}/draft/save-version` | `{version_id, number, snapshot_id}` — черновик как есть становится новой головной версией (`prompt_text` = «Правки сохранены владельцем», без запуска ИИ и проверки сборки) |
| `POST` | `/api/projects/{project_id}/draft/discard` | `{written, deleted, workspace_revision}` — файлы черновика переписаны на сохранённую версию, база и версии не меняются |

Ошибки (`error.code` / `details.reason`): `generation_active` — идёт сборка; `restoration_active` — идёт
подготовка отката; `conflict` + `reason=draft_clean` — черновик уже совпадает с версией; `conflict` +
`reason=workspace_asleep` — среда спит, откройте предпросмотр; `conflict` + `reason=draft_changed` —
черновик изменился во время действия. Оркестратор: `GET/POST /internal/workspaces/{id}/draft/files|reset`
(без аренды генерации; при активной аренде — 409).

## Версионирование

Пока не вводим `/api/v1` префикс — это будет добавлено перед публичным запуском бета-теста. До тех пор — breaking changes согласовываем через инбокс координации.
