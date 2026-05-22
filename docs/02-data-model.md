# 02. Data Model — Postgres схема

Эталонная схема БД. Агент B пишет миграции через Alembic в `apps/api/migrations/`. Любое изменение схемы = миграция + правка этого файла.

## Конвенции

- Все ID — `UUID` (Postgres `uuid` type, генерация на стороне приложения через `uuid4()` для testability).
- Время — `TIMESTAMPTZ` (не `TIMESTAMP`).
- Удаление — soft (поле `deleted_at TIMESTAMPTZ NULL`) только там, где это явно нужно. Иначе — hard `DELETE` с каскадом.
- Деньги — `NUMERIC(12, 4)` для рублей (4 знака для долей копеек при списании за токены).
- Текст — `TEXT` (не `VARCHAR(N)` — в Postgres разницы нет, но `TEXT` гибче).

## Таблицы

### `users`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `email` | citext | UNIQUE NOT NULL |
| `password_hash` | text | NOT NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `last_login_at` | timestamptz | NULL |

`citext` — case-insensitive (через `CREATE EXTENSION citext`).

### `projects`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `owner_id` | uuid | FK → `users(id)` ON DELETE CASCADE |
| `name` | text | NOT NULL, CHECK length BETWEEN 1 AND 100 |
| `slug` | text | UNIQUE NOT NULL — для `/p/:slug` |
| `template` | text | NOT NULL, CHECK IN ('blank', 'landing', 'portfolio', 'blog') |
| `current_snapshot_id` | uuid | FK → `snapshots(id)` (deferred — задаётся после создания первого snapshot) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() — тригер на UPDATE |

**Индексы:** `(owner_id, created_at DESC)` для списка проектов.

### `snapshots`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `project_id` | uuid | FK → `projects(id)` ON DELETE CASCADE |
| `commit_sha` | text | NOT NULL, CHECK length = 40 |
| `prompt_text` | text | NULL (null для initial и rollback-снапшотов) |
| `model_id` | text | NULL |
| `parent_id` | uuid | FK → `snapshots(id)` NULL (null у initial) |
| `preview_key` | text | NULL — путь в MinIO, заполняется после Playwright |
| `is_rollback_target` | bool | NOT NULL DEFAULT false |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Индексы:** `(project_id, created_at DESC)` для timeline.

### `messages`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `project_id` | uuid | FK → `projects(id)` ON DELETE CASCADE |
| `snapshot_id` | uuid | FK → `snapshots(id)` NULL |
| `role` | text | NOT NULL, CHECK IN ('user', 'assistant', 'system') |
| `content` | text | NOT NULL |
| `model_id` | text | NULL |
| `tokens_in` | integer | NULL |
| `tokens_out` | integer | NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Индексы:** `(project_id, created_at ASC)` для чата.

### `wallets`
| Поле | Тип | Constraints |
|---|---|---|
| `user_id` | uuid | PK, FK → `users(id)` ON DELETE CASCADE |
| `balance_rub` | numeric(12, 4) | NOT NULL DEFAULT 100.0000 (стартовый баланс 100₽ для MVP) |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() |

### `wallet_charges`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users(id)` ON DELETE CASCADE |
| `message_id` | uuid | FK → `messages(id)` NULL (null для topup) |
| `amount_rub` | numeric(12, 4) | NOT NULL — отрицательное = списание, положительное = пополнение |
| `description` | text | NOT NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Индексы:** `(user_id, created_at DESC)`.

### `usage` (детальный лог токенов)
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users(id)` ON DELETE CASCADE |
| `project_id` | uuid | FK → `projects(id)` ON DELETE SET NULL |
| `message_id` | uuid | FK → `messages(id)` ON DELETE SET NULL |
| `model_id` | text | NOT NULL |
| `tokens_in` | integer | NOT NULL |
| `tokens_out` | integer | NOT NULL |
| `cost_rub` | numeric(12, 4) | NOT NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

Эту таблицу пишет **LLM Gateway** (агент C) после каждого запроса. Отдельно от `wallet_charges`, потому что usage — аналитика, charges — финансы (могут быть несоответствия и их надо видеть отдельно).

### `github_connections` (Export to GitHub)
| Поле | Тип | Constraints |
|---|---|---|
| `user_id` | uuid | PK, FK → `users(id)` ON DELETE CASCADE |
| `access_token_encrypted` | text | NOT NULL — Fernet-шифр OAuth-токена (**не** плейнтекст) |
| `github_username` | text | NOT NULL |
| `scopes` | text | NULL |
| `connected_at` | timestamptz | NOT NULL DEFAULT now() |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() — триггер на UPDATE |

1:1 к user (как `wallets`). Токен хранится только зашифрованным.

**`projects` (+github-export):** добавлены `github_repo_full_name text NULL`, `github_repo_url text NULL`, `github_last_pushed_at timestamptz NULL` — зеркало последнего экспорта.

## Что хранится НЕ в Postgres

| Данные | Где | Почему |
|---|---|---|
| Файлы проектов (git-объекты) | MinIO bucket `projects/{project_id}/` (bare repo) | S3-совместимо, дёшево для blob, нативно поддерживается pygit2 |
| PNG-превью | MinIO bucket `previews/{snapshot_id}.png` | То же |
| Сессии (если будем стейтфул) | Redis | низкая латентность; в MVP — JWT, без серверного state |
| LLM-кеш | Redis с TTL 1 час, ключ `llm:cache:{sha256}` | В Postgres было бы избыточно |
| Очередь preview | Redis (RQ) | RQ нативно поверх Redis |

## Миграции — порядок

| # | Что | Кто пишет |
|---|---|---|
| `0001` | extensions (citext, uuid-ossp), `users`, `wallets` | агент B (M0) |
| `0002` | `projects`, `snapshots`, `messages` | агент B (M1) |
| `0003` | `wallet_charges`, `usage` + индексы | агент B (M2) |
| `0005` | `github_connections` + `projects.github_*` (Export to GitHub) | github-export |

## Trigger для `updated_at`

```sql
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER projects_updated_at BEFORE UPDATE ON projects
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

(Применить и к `wallets`.)

## Правила целостности

- Создание проекта = транзакция: `INSERT projects` → инициализация bare repo в MinIO → первый `INSERT snapshots` (initial commit с шаблоном) → `UPDATE projects.current_snapshot_id`. Если любой шаг падает — откат всего.
- Списание за токены = транзакция: `INSERT usage` + `UPDATE wallets.balance_rub` + `INSERT wallet_charges`. Если `balance_rub < 0` — откат и ответ клиенту `wallet_empty`.
- `current_snapshot_id` всегда указывает на последний валидный snapshot этого проекта.

## ER-диаграмма (для головы)

```
users ─┬─< projects ─< snapshots ─┐ (parent_id, само-FK)
       │                          │
       ├─< messages ──────────────┘
       │
       ├─ wallets (1:1)
       └─< wallet_charges, usage
```
