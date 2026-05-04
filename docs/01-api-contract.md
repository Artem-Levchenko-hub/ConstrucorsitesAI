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
| `POST` | `/api/projects` | `{name, template?: "blank"\|"landing"\|"portfolio"\|"blog"}` | `Project` |
| `GET` | `/api/projects` | — | `Project[]` (только свои) |
| `GET` | `/api/projects/:id` | — | `Project` |
| `DELETE` | `/api/projects/:id` | — | 204 |

### Промпт и снапшоты

| Метод | Path | Тело | Ответ |
|---|---|---|---|
| `POST` | `/api/projects/:id/prompt` | `{prompt: string, model_id: string}` | `{message_id, snapshot_id?}` (snapshot_id появится позже через WS) |
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
| `GET` | `/p/:slug` | `index.html` текущего HEAD |
| `GET` | `/p/:slug/*` | статика проекта (CSS, JS, img) |

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
  "model": "claude-sonnet-4-6" | "gpt-4.1" | "yandexgpt-5" | "qwen-3-coder",
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
  slug: string;              // для /p/:slug
  template: "blank" | "landing" | "portfolio" | "blog";
  current_snapshot_id: string | null;
  created_at: string;
  updated_at: string;
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

export type Message = {
  id: string;
  project_id: string;
  snapshot_id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
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
        | "rate_limited" | "wallet_empty" | "model_unavailable" | "internal_error";
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
| Остальные | 60/мин на user |

Заголовки: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

## Версионирование

Пока не вводим `/api/v1` префикс — это будет добавлено перед публичным запуском бета-теста. До тех пор — breaking changes согласовываем через инбокс координации.
