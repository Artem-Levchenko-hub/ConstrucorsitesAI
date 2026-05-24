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

### Projects

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects` | `{name, kind?: "static"\|"fullstack", template?: <см. ниже>}` | `Project` |
| `GET` | `/api/projects` | — | `Project[]` (только свои) |
| `GET` | `/api/projects/:id` | — | `Project` |
| `DELETE` | `/api/projects/:id` | — | 204 (orchestrator destroy для fullstack) |

`kind` (V2): `static` → V1 шаблоны (`blank/landing/portfolio/blog`). `fullstack` → V2 шаблоны (`nextjs-postgres-drizzle`, далее — `nextjs-supabase`, `fastapi-postgres`, `nextjs-resend`, `telegram-bot-python` и т.д.). Дефолт — `static` для backward-compatibility до полного V2 launch.

### Промпт и снапшоты

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects/:id/prompt` | `{prompt: string, model_id: string, selected_elements?: SelectedElement[]}` | `{message_id, snapshot_id?}` (snapshot_id появится позже через WS) |
| `GET` | `/api/projects/:id/snapshots` | — | `Snapshot[]` (DESC по `created_at`) |
| `GET` | `/api/projects/:id/snapshots/:sid` | — | `Snapshot & { files: { [path]: string } }` |
| `POST` | `/api/projects/:id/rollback` | `{snapshot_id}` | `Snapshot` (новый — результат отката) |
| `GET` | `/api/projects/:id/messages` | `?limit=50&before=<msg_id>` | `Message[]` |

### Wallet (MVP — без реальной оплаты)

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `GET` | `/api/wallet` | — | `{balance_rub: number, recent_charges: Charge[]}` |
| `POST` | `/api/wallet/topup` | `{amount_rub}` | `{balance_rub}` (MVP-stub: всегда успех) |

### Models (для селектора в UI)

| Метод | Path | Ответ |
|---|---|---|
| `GET` | `/api/models` | `Model[]` — список с ценами в ₽/1k токенов |

### Public preview (без auth)

| Метод | Path | Ответ |
|---|---|---|
| `GET` | `/p/:slug` | `index.html` текущего HEAD (только `kind=static`) |
| `GET` | `/p/:slug/*` | статика проекта (CSS, JS, img) |

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

Не доступно публично. Auth: header `X-Internal-Token` (shared secret). Полный контракт — `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`. Краткий список:

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

## WebSocket: `/api/ws/projects/:id`

**Подключение:** `ws://localhost:8000/api/ws/projects/:id` с cookie `omnia_session` или `?token=<jwt>`.

**Server → client события:**

```json
{ "type": "snapshot.created", "data": { "snapshot": Snapshot } }
{ "type": "preview.ready",   "data": { "snapshot_id": "uuid", "preview_url": "https://.../previews/uuid.png" } }
{ "type": "llm.chunk",       "data": { "message_id": "uuid", "delta": "...текст" } }
{ "type": "llm.done",        "data": { "message_id": "uuid", "tokens_in": 1200, "tokens_out": 4500, "cost_rub": 3.75 } }
{ "type": "llm.error",       "data": { "message_id": "uuid", "error": "string" } }
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
        | "port_exhausted" | "conflict";
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
| Остальные | 60/мин на user |

Заголовки: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

## Версионирование

Пока не вводим `/api/v1` префикс — это будет добавлено перед публичным запуском бета-теста. До тех пор — breaking changes согласовываем через инбокс координации.
