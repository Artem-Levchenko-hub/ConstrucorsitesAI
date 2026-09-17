# P00–P03 — почему сгенерированное MAX-приложение не сохраняет данные (17.09.2026)

**План:** `2026-09-17_MAX_Studio_restore_data_persistence_plan.md`, этапы P00–P03.

**Режим:** только чтение. Production не менялся: контейнеры не запускались и не
останавливались, миграции, `.env`, ключи и данные не трогались. Код сгенерированного
приложения прочитан напрямую из Docker volume. Значения секретов не выводились.

**Запущенный код платформы:** api/worker/orchestrator `d3931113` (см.
`2026-09-17-t22-1-production-baseline.md`). Исходники разобраны на этом же SHA.

## 1. Паспорт среды (P01)

| Поле | Значение |
|---|---|
| Платформенные миграции | `alembic_version = 0062_project_cell_rollout` (0061 и 0062 применены) |
| MAX-проекты с 10.09 | 3: `fitnessstat-8f97ff` (генерация отменена), `klienty-422c35`, `smena-demo-kofeini-dlia-lendinga-2417f5` (генерация упала `PROVIDER_AUTH_FAILED`, 14.09 12:06 UTC) |
| Исследуемое приложение | `klienty-422c35`, project `48d8c361…`, cell workspace `d53afe37…` (`docker_owner_canary`, state `stopped`) |
| Генерации | `47ee1297` build completed (14.09 06:32–06:49 UTC, модель `gemini-3.1-pro-preview-customtools`); затем 5 отменённых; `ab7d7495` completed (QA-правка надписей) |
| Хранилища | project DB volume `omnia-machine-d53afe37…-app-postgres-data`; cell Postgres `omnia-cell-d53afe37…-postgres` (остановлен); workspace volume `omnia-cell-d53afe37…-workspace` |
| Vault / ключи данных | `CELL_DATA_VAULT_ADDRESS` и остальные `CELL_DATA_*` **не заданы** ни в текущем `apps/orchestrator/.env`, ни в одной из 40+ резервных копий; Vault-сервиса/контейнера на хосте нет |
| Core images | `CELL_PREVIEW_CORE_IMAGE`, `CELL_PUBLIC_CORE_IMAGE` заданы |

## 2. Цепочка записи «Клиентов» (по сохранённому коду)

1. `src/app/page.tsx` хранит клиентов **только** через
   `secureCollection("clients")` (`create`/`update`/`list`). Обычной таблицы клиентов нет,
   `.omnia/data-contract.json` нет; `src/lib/db/schema.ts` содержит лишь стартовые таблицы kit.
2. Запрос идёт в `/api/omnia/data/[...path]` → `createSecureDataHandler` →
   `openSecureDataStore()`.
3. `openSecureDataStore` требует `OMNIA_PROJECT_ID` **и** `OMNIA_DATA_KEY_FILE`, иначе бросает
   «Secure data keys are unavailable»; обработчик отвечает `503 KEY_UNAVAILABLE`.
4. `OMNIA_DATA_KEY_FILE` выставляется только если `prepare_core_keys` получил ключ из Vault
   (`app_data_runtime.py:59–75`). При пустом адресе Vault он возвращает `None`.
5. На странице любая ошибка сохранения показывает «Не удалось сохранить запись. Проверьте
   соединение и повторите», а загрузка списка — состояние `error`.

**Вывод:** на этом сервере каждая операция с клиентами в этом приложении обязана завершаться
отказом `KEY_UNAVAILABLE`: хранилище, на которое опирается код, здесь никогда не было настроено.
Ложного «Сохранено» нет — ошибка честная, но данные не сохраняются вовсе.

## 3. Почему генерация выбрала неработающий путь

- `secure_data_crud` вычисляется только из `cell_data_vault_address`
  (`machine_adapter.py:76`) → на этом сервере `False`, и `SECURE_DATA_GUIDE` агенту не выдаётся
  (`portable_cell_contract.py:159–160`).
- Но MAX-kit (`apps/api/src/omnia_api/services/max_project_kit.py:20,26`) **всегда** кладёт в
  проект `src/lib/omnia/data-client.ts` с `secureCollection`, маршрут `/api/omnia/data` и
  `src/lib/secure-data/*`. Ни файл, ни инструкция не сообщают, что на этой среде путь выключен.
- С 4eebed7c новая БД проекта сразу защищена: приложение работает как `omnia_runtime` без DDL,
  с пустым контрактом, а создать таблицу можно только через `omnia-db apply` контракта.
  Инструкция при этом говорит «Use the dedicated DATABASE_URL above for product data».
- Агент не создал контракт и таблицу, а использовал готовый `secureCollection` из kit.
- Финализация генерации (`build` + `runtime_check`) это не остановила: сборка `47ee1297`
  получила `completed`.

Это соответствует гипотезам H01 (обещанный/доступный secure CRUD без готовой инфраструктуры) и
H02 (ограничения DDL без созданной схемы) из плана, а также разрыву P06 (нет проверки
реальной записи в финальном gate).

**Независимое подтверждение:** 14.09 07:58 UTC владелец сам поставил генерации задачу
«заменить неработающий Vault-only `secureCollection("clients")` на обычный plaintext CRUD в
выделенной PostgreSQL проекта». Эта и три следующие попытки отменены; текущий код по-прежнему
Vault-only.

## 4. Что ещё не доказано (нужно для закрытия P02)

- [ ] Живой HTTP-ответ сохранения (`503 KEY_UNAVAILABLE`) — среда `klienty` остановлена; запуск
  её на production — действие записи, требует согласования (лучше воспроизвести сохранённый код
  на QA-проекте).
- [ ] Наличие `data-policy.json` / `initial-database.json` и фактический `database_admin` для
  `d53afe37` (файлы не найдены за время короткого поиска; полный поиск по диску прерван, чтобы
  не нагружать общий хост).
- [ ] Есть ли строки в `omnia_secure_records` (cell Postgres остановлен).
- [ ] Отдельный блокер генерации: `PROVIDER_AUTH_FAILED` у последнего запуска 14.09 — проверить
  ключ провайдера модели до любых новых прогонов.

## 5. Минимальные кандидаты исправления (P04, требуют решения)

- **A (точечно, рекомендуется первым):** не выдавать generated-проекту Vault-only интерфейс, когда
  secure CRUD не готов. Либо не класть `data-client.ts`/`/api/omnia/data`/`secure-data/*` в kit при
  `secure_data_crud != True`, либо явно сообщать агенту «secure CRUD недоступен, используй
  data-contract + DATABASE_URL». Плюс финальный gate: реальная запись→чтение (P06), чтобы
  `KEY_UNAVAILABLE` не давал `completed`.
- **B:** закрепить для новых MAX-проектов обычный PostgreSQL-профиль (контракт через
  `omnia-db apply`) до развёртывания Vault; существующие secure-записи не трогать.
- Настроить Vault «ради зелёного» не предлагается без отдельного решения: это новый контур,
  отложенный планом.

Failing-тест для A: при `secure_data_crud=False` kit/инструкция не предлагают `secureCollection`,
а финальный gate проваливает генерацию, чья запись возвращает `KEY_UNAVAILABLE`.
