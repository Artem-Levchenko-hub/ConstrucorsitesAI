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

### `generation_runs` (миграция `0025`)

Durable-идентичность и жизненный цикл одного принятого `POST /prompt`.

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `project_id` | uuid | FK → `projects(id)` ON DELETE CASCADE, NOT NULL |
| `user_id` | uuid | FK → `users(id)` ON DELETE CASCADE, NOT NULL |
| `assistant_message_id` | uuid | FK → `messages(id)` ON DELETE SET NULL, NULL до создания пары сообщений |
| `idempotency_key` | text | NOT NULL; UNIQUE вместе с `project_id` |
| `prompt_hash` | text | NOT NULL; защищает от переиспользования ключа с другим payload |
| `status` | text | NOT NULL; CHECK IN (`pending`, `running`, `cancel_requested`, `cancelled`, `completed`, `failed`) |
| `response_mode` | text | NULL (`build`, `edit`, `clarify`) |
| `response_payload` | jsonb | NULL; точный ответ для идемпотентного replay |
| `error` | text | NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `started_at` | timestamptz | NULL |
| `finished_at` | timestamptz | NULL |

**Инварианты и индексы:**

- `UNIQUE (project_id, idempotency_key)` — один submit не создаёт две генерации.
- Partial `UNIQUE (project_id) WHERE status IN ('pending','running','cancel_requested')`
  — не более одной активной генерации проекта, включая разные вкладки/API-процессы.
- `(project_id, created_at)` — история запусков.
- При старте единственного API-процесса оставшиеся активными строки завершаются
  как `failed`: process-local coroutine не может пережить рестарт.

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
| `subscription_id` | uuid | NULL, FK → `subscriptions(id)` ON DELETE SET NULL |
| `entry_type` | text | `usage`, `topup`, `payment`, `refund`, `subscription_credit`, `adjustment` |
| `amount_rub` | numeric(12, 4) | NOT NULL — отрицательное = списание, положительное = пополнение |
| `balance_after_rub` | numeric(12, 4) | NOT NULL — баланс сразу после операции |
| `external_ref` | text | NULL, UNIQUE — идемпотентная ссылка вида `usage:<uuid>` или `payment:<uuid>` |
| `description` | text | NOT NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Индексы:** `(user_id, created_at DESC)`, `(subscription_id)`.

`wallet_charges` — единственный финансовый журнал кошелька. Старая
`wallet_ledger_entries` удалена миграцией `0035`, а её записи перенесены сюда.
Текущий баланс остаётся в `wallets` для быстрого чтения; журнал нужен для
аудита, экспорта и восстановления последовательности операций. Переходный
`BEFORE INSERT`-триггер подставляет текущий баланс, если старый экземпляр
сервиса во время rolling deploy ещё не передал `balance_after_rub`.

### `billing_plans`

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `code` | text | Логический тариф: `free`, `pro`, `business` |
| `version` | integer | Версия коммерческих условий |
| `price_rub` | numeric(12, 2) | Цена периода |
| `billing_interval` | text | Сейчас только `month` |
| `included_credit_rub` | numeric(12, 4) | Кредит кошелька на период |
| `entitlements` | jsonb | Лимиты проектов, публикаций, команд и интеграций |
| `is_active` | boolean | Только одна активная версия на `code` |

`UNIQUE(code, version)` сохраняет историю условий. Цена или лимиты не
перезаписываются: создаётся новая версия, а действующие подписки остаются на
старой. Триггер БД разрешает у существующего тарифа изменить только
`is_active`; попытка переписать цену, лимиты или номер версии отклоняется.

### `subscriptions`

| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users(id)` ON DELETE RESTRICT |
| `plan_id` | uuid | FK → `billing_plans(id)` ON DELETE RESTRICT |
| `payment_method_id` | uuid | NULL, FK → `billing_payment_methods(id)` |
| `status` | text | `trialing`, `active`, `past_due`, `paused`, `canceled`, `expired` |
| `auto_renew` | boolean | По умолчанию `false` до реализации рекуррентных списаний |
| `cancel_at_period_end` | boolean | Отмена в конце текущего периода |
| `current_period_start/end` | timestamptz | NULL для бессрочного Free |
| `next_charge_at` | timestamptz | Следующая попытка продления |
| `grace_period_ends_at` | timestamptz | Конец льготного периода после ошибки оплаты |
| `canceled_at`, `ended_at` | timestamptz | Аудит завершения |

Partial unique index по `user_id` разрешает не более одной живой подписки
(`trialing`, `active`, `past_due`, `paused`). При регистрации создаётся Free;
анонимные технические пользователи подписку не получают.

### `billing_payment_methods`

Хранит только токен платёжного провайдера, статус и зафиксированное согласие на
повторные списания. Данные карты в Omnia не попадают. Таблица подготовлена для
автопродления, но сам процесс продления ещё не включён.

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

## V3 расширения (multi-stack + onboarding + linked-repo)

> Полный design — `docs/10-v3-multistack-pivot.md`. Изменения **additive**: legacy V1/V2-проекты с `stack_id=NULL` продолжают работать через старый код-путь.

### `projects` — добавленные колонки (миграция `0008`)

| Поле | Тип | Constraints |
|---|---|---|
| `stack_id` | text | NULL, FK → `stack_templates(id)`, ON DELETE SET NULL. NULL для legacy V1/V2. |
| `preset_id` | text | NULL, FK → `ui_kit_freeze(slug)` ON DELETE SET NULL. Дублирует `design_preset_id` (миграция 0007) только в случае выбора из freeze-БД; иначе тут NULL, а fallback читается через старый `design_preset_id`. |
| `onboarding_session_id` | uuid | NULL, FK → `onboarding_sessions(id)` ON DELETE SET NULL. |
| `linked_repo_id` | uuid | NULL, FK → `linked_repos(id)` ON DELETE SET NULL. |
| `estimated_setup_cost_rub` | numeric(12, 4) | NULL — справочно, не транзакционно. |

### `stack_templates`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | text | PK (e.g. `static-html`, `nextjs-postgres-drizzle`) |
| `display_name` | text | NOT NULL |
| `description` | text | NOT NULL |
| `when_to_use` | text | NOT NULL — LLM-критерий для recommend |
| `priority` | text | NOT NULL, CHECK IN ('P0','P1','P2') |
| `template_dir` | text | NOT NULL — путь относительно `apps/orchestrator/templates/` |
| `supported_features` | text[] | NOT NULL DEFAULT '{}' — `{ssr,db,auth,realtime,ml,...}` |
| `is_active` | bool | NOT NULL DEFAULT true |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Seed:** `apps/api/src/omnia_api/seed/stack_templates.py` (Chat-2). Минимум P0 на launch — 3 записи.

### `ui_kit_freeze`
| Поле | Тип | Constraints |
|---|---|---|
| `slug` | text | PK (e.g. `palette-editorial-trust`, `font-pair-saas-modern`) |
| `source` | text | NOT NULL, CHECK IN ('ui-ux-pro-max','context7','manual','design-presets-v2-fallback') |
| `category` | text | NOT NULL, CHECK IN ('palette','font_pair','pattern','component','framework_docs') |
| `name` | text | NOT NULL |
| `payload` | jsonb | NOT NULL — формат зависит от category, см. `docs/10` |
| `applicable_stacks` | text[] | NOT NULL DEFAULT '{}' (пустой = универсальный) |
| `applicable_presets` | text[] | NOT NULL DEFAULT '{}' (legacy preset_ids из docs/09) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() — тригер `set_updated_at` |

**Индексы:** `(category)`, `(source)`, GIN `(applicable_stacks)`, GIN `(applicable_presets)`.

**Seed:** `apps/api/src/omnia_api/seed/ui_kit_freeze.py` (Chat-2 пишет файл, **я** запускаю руками с экспортом из плагина ui-ux-pro-max).

### `linked_repos`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users(id)` ON DELETE CASCADE |
| `provider` | text | NOT NULL DEFAULT 'github', CHECK IN ('github') — V3 только github |
| `github_user_id` | bigint | NOT NULL |
| `github_username` | text | NOT NULL |
| `repo_full_name` | text | NULL — заполняется на confirm-repo, `owner/name` |
| `branch` | text | NOT NULL DEFAULT 'omnia/deploy' |
| `access_token_encrypted` | bytea | NOT NULL — зашифровано Fernet/AES-GCM, ключ в `.env` `LINKED_REPO_ENCRYPTION_KEY` |
| `scopes` | text[] | NOT NULL DEFAULT '{}' — GitHub OAuth scopes (`repo`, `read:user`, ...) |
| `connected_at` | timestamptz | NOT NULL DEFAULT now() |
| `last_push_at` | timestamptz | NULL |
| `revoked_at` | timestamptz | NULL — юзер сделал DELETE /repo |

**Индексы:** `(user_id, connected_at DESC)`, UNIQUE `(user_id, github_user_id)`.

### `onboarding_sessions`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `users(id)` ON DELETE CASCADE |
| `state` | text | NOT NULL, CHECK IN ('asking-Q','recommending-stack','recommending-preset','complete','abandoned') |
| `brief` | text | NOT NULL — первое описание идеи |
| `step` | integer | NOT NULL DEFAULT 1 |
| `max_steps` | integer | NOT NULL DEFAULT 5 |
| `current_question` | text | NULL — текущий вопрос для UI |
| `why` | text | NULL — обоснование от Haiku |
| `stack_recommendations` | jsonb | NULL — `[{stack_id,score,reasoning}]` после recommending-stack |
| `chosen_stack_id` | text | NULL, FK → `stack_templates(id)` ON DELETE SET NULL |
| `chosen_preset_id` | text | NULL, FK → `ui_kit_freeze(slug)` ON DELETE SET NULL |
| `linked_repo_id` | uuid | NULL, FK → `linked_repos(id)` ON DELETE SET NULL |
| `project_id` | uuid | NULL, FK → `projects(id)` ON DELETE SET NULL — заполняется на complete |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() — тригер |

**Индексы:** `(user_id, created_at DESC)`, `(state)` (для cleanup-job по abandoned).

### `onboarding_messages`
| Поле | Тип | Constraints |
|---|---|---|
| `id` | uuid | PK |
| `session_id` | uuid | FK → `onboarding_sessions(id)` ON DELETE CASCADE |
| `role` | text | NOT NULL, CHECK IN ('system','ai','user') |
| `content` | text | NOT NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Индексы:** `(session_id, created_at ASC)`.

### `wallet_charges` — расширение `type` enum

Добавить новые значения (если type — enum). Если text+CHECK — расширить CHECK:

```sql
ALTER TABLE wallet_charges
  DROP CONSTRAINT wallet_charges_type_check;
ALTER TABLE wallet_charges
  ADD CONSTRAINT wallet_charges_type_check
  CHECK (type IN ('tokens','runtime_hours','deploy_slot','domain','topup','onboarding','deploy_link'));
```

### `usage` — расширение `purpose` колонкой (если ещё нет)

Если `usage` не содержит `purpose` — добавить:

```sql
ALTER TABLE usage ADD COLUMN purpose text NULL;
COMMENT ON COLUMN usage.purpose IS
  'V3: семантика вызова — "prompt" (default, V1), "onboarding_q", "stack_recommend", "preset_classify"';
```

Это позволит группировать аналитику по Haiku-затратам на онбординг отдельно от Sonnet/Opus генерации сайтов.

### Миграция (порядок продолжается)

| # | Что | Кто пишет |
|---|---|---|
| `0008` | V3: projects(+5 cols) + stack_templates + ui_kit_freeze + linked_repos + onboarding_sessions + onboarding_messages + wallet_charges.type расширение + usage.purpose | Chat-2 (single atomic migration) |

После 0008 Chat-2 запускает seed-скрипты вручную в dev: `python -m omnia_api.seed.stack_templates` + `python -m omnia_api.seed.ui_kit_freeze` (последний я добиваю реальными данными отдельно).

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
| `0025` | `generation_runs`: idempotency + single-flight + cancellation lifecycle | Codex |
| `0030` | ЮKassa payments, юридические согласия, бизнес-профили и отдельный legacy ledger | Codex |
| `0035` | единый `wallet_charges`, версии тарифов, подписки и токены способов оплаты | Codex |

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
- Любое движение кошелька = одна транзакция: блокировка/условное обновление `wallets` + `INSERT wallet_charges`; расход модели дополнительно пишет `usage`. Если средств не хватает — вся транзакция откатывается.
- Успешный webhook провайдера и refund используют уникальный `external_ref`, поэтому повторное событие не меняет баланс второй раз.
- У пользователя не может быть двух живых подписок одновременно; подписка всегда указывает на конкретную версию тарифа.
- `current_snapshot_id` всегда указывает на последний валидный snapshot этого проекта.

## ER-диаграмма (для головы)

```
users ─┬─< projects ─< snapshots ─┐ (parent_id, само-FK)
       │                          │
       ├─< messages ──────────────┘
       ├─< generation_runs ───────┘ (assistant_message_id)
       │
       ├─ wallets (1:1)
       ├─< subscriptions >─ billing_plans
       │          └─ billing_payment_methods
       └─< wallet_charges, usage, payments
```
