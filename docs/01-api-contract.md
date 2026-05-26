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

## Версионирование

Пока не вводим `/api/v1` префикс — это будет добавлено перед публичным запуском бета-теста. До тех пор — breaking changes согласовываем через инбокс координации.
