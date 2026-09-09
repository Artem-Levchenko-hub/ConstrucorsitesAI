# START HERE — безопасный рефакторинг Omnia

Актуально на 9 сентября 2026. [Полный план](2026-09-08-safe-total-refactoring-plan.md).
Реализация, проверки, независимое review и доставка — только GPT-6 Astra.

## Приоритет владельца

Меньше поддерживаемого кода, минимализм и меньше контекста для следующих агентов.
Выбирать устранение доказанных дублей, лишних действий и ненужных прослоек.
Сам перенос функции не считать сокращением: production source измерять отдельно
от тестов, документов и готовых пользовательских файлов, сохраняя читаемость.

Task11 preview runtime сократил PreviewFrame на46 строк, добавив hook65 строк:
итог **+19 строк рабочего кода**. Это отделение обязанности, без доказанного
сокращения или ускорения. Следующий простой перенос envelope→AgentStep отложен
после уточнения владельца; новый продуктовый код для него не писался.

## Доставлено

| Пакет | Граница и доказательства |
| --- | --- |
| P01 / Task2 | Metadata-only PostgreSQL probe. [Проверка](2026-09-08-p01-postgres-probe-verification.md). |
| Task5 | Общие template CSS/JS, прежние standalone outputs. [Проверка](2026-09-08-task5-template-verification.md), [baseline corrections](2026-09-08-baseline-corrections.md). Не повторять. |
| Task5 extension | Одна anime вместо четырёх: −52 181 bytes/−25 строк; прежние файлы и custom export/rollback. [Проверка и доставка](2026-09-09-task5-anime-verification.md). |
| Task4 | Убраны повторные запросы истории. [Проверка](2026-09-08-task4-history-verification.md). |
| Task8 | Общая SQL-подготовка artifacts, прежняя транзакция. [Проверка](2026-09-08-task8-artifacts-verification.md). |
| Task9 | Cancellation helper в существующем service. [Проверка](2026-09-08-task9-cancellation-verification.md). |
| Task10A/10B | Exact-edit validator и browser-container список. [10A](2026-09-08-task10-exact-edit-verification.md), [10B](2026-09-08-task10b-browser-container-verification.md). |
| Task11 transport | Отдельный WebSocket transport. [Проверка](2026-09-09-task11-transport-verification.md). |
| Task11 preview runtime | usePreviewRuntime, web496/78, focused74, browser1440/390, Astra No findings. [Проверка и доставка](2026-09-09-task11-preview-runtime-verification.md). |
| Task11 message cache | Девять повторов обновления сообщения заменены одной локальной функцией: −32 строки/−1101 нормализованный байт; web502/78, browser1440/390. [Проверка и доставка](2026-09-09-task11-message-cache-verification.md). |
| Task3 usage buckets | Убран повтор начальных значений стадий: −14 строк / −320 байт. До/после проходят шесть JSON-сценариев и два отказа 404/409; полный API3272. SQL и арифметика прежние. [Проверка и доставка](2026-09-09-task3-usage-buckets-verification.md). |

## Текущая поставка

- Web `c2da4f71ae8a61faf5c7e3995350f5c850b397f2`, код изменения `bf428456`, image
  `sha256:d72f73045b6306a782e0028b075314120d58ff03965d474c5440f6bc0f24d896`.
  Exact archive/image startup, CI34365432898 web/image, локальный web502/78,
  types/lint, browser1440/390 и Astra review прошли. Runtime identity/healthy,
  API6/6, четыре публичных маршрута200 и снятый gate405 подтверждены.
- API `7c68920394ca03f2bab637235d0d5ee3468ac242`, image
  `sha256:aa9c68f7535671e009ee2589331af1b780712123d2ffe3b37d540a322e11ddff`.
  CI34368867947: все7 jobs success, полный API **3272 passed /12 skipped /8 xfailed**.
  Новый контейнер healthy, release/image совпадают, 0 restarts; API6/6 и четыре
  публичных маршрута200 подтверждены 2026-09-09 15:42:07 UTC.
- Worker/generation-worker `50565efd`, image
  `sha256:47ac83c1f70ee7351812128fcfc56869e8bdd020fe3143ff45207c4fb2cf4664`;
  orchestrator `b99af471`. API-only поставка сохранила семь остальных контейнеров
  (ID/image/StartedAt/Status), host PID/start и пять dirty документов. Общие
  `API_IMAGE` и release metadata меняют desired Compose configuration, но эти
  сервисы не пересоздавались. Разные component SHA ожидаемы и проверены.
- API запись: `/opt/omnia-runtime/releases/task3-usage-buckets-7c689203/result.json`.
  Web запись: `/opt/omnia-runtime/releases/task11-message-cache-c2da4f71-retry1/result.json`.
  Две прежние ошибки из-за ручных остановок backend сохранены в verification doc;
  параллельная задача завершилась до новой попытки. Разные component SHA ожидаемы:
  общая compose SHA меняет desired metadata, но обновлялся только web.
- H149 опубликован: public version81, сохранены145 прежних гипотез. H150 добавляется
  отдельно с HTTP readback; запись `publication.json` рядом с соответствующим
  runtime result. Не заменять публичный отчёт всем репозиторным JSON.

## Следующий шаг

Task11 message cache и локальное сокращение `get_max_usage` доставлены;
их и прежние Task5 пакеты не повторять. Эти два пакета убрали46 строк рабочего
кода без смены контрактов. После нового Task0 выбирать следующий R-пакет только
по доказанному дублю и baseline реального consumer; новый перенос между файлами
без сокращения не считать достижением цели.

SQL-агрегация Task3 остаётся открытой: настоящий сериализатор сохраняет `0.1+0.2` как
`0.30000000000000004`, Decimal SUM дал бы `0.3`. SQL/точность/порядок нельзя
менять по умолчанию. Task6/7 требуют model smoke, который владелец запускает сам;
Task12 — измеренной причины IO задержки. Cleanup/stale-probe usePromptStream
остаются отдельным B-пакетом. [Матрица20](2026-09-09-refactoring-scope-review.md)
сохраняет gaps: весь план и оценка10/10 не объявлены завершёнными.

## Правила продолжения

1. Проверить root/branch/upstream/status, fetch all prune, local/remote/server SHA.
   Чужие изменения не stash/reset/rebase. Рабочая копия:
   `C:/Users/79133/ConstrucorsitesAI-refactor-preview-20260909`, ветка
   `codex/refactor-preview-lifecycle-20260909`, upstream `origin/main`.
2. Один writer, только Astra; независимое review и честная фиксация недоступных
   проверок. По одному пакету до полного delivery loop.
3. Сохранять UX/тексты, историю/данные, API/ошибки/списания, permissions,
   fences/leases, native/text/one-shot и действующие запреты, включая MAX restore409.
   Не включать B/N, новые зависимости, миграции или общий Docker prune.
4. API tests — только disposable DB: conftest очищает схему. Web CI —
   Node20/pnpm9.15.0. Node25 Web Storage failures не чинить продуктовым кодом;
   Windows packaging может дать EPERM, тогда обязателен Linux image gate.
5. SSH `i48ptgvnis@170.168.72.200`, repo `/opt/omnia`; production compose
   `apps/llm-gateway/deploy/full/docker-compose.yml`, project `full`.
   Orchestrator — host `omnia-orchestrator.service`. `infra/` не production.
6. Серверные пять dirty secondbrain документов, env backups и пользовательские
   данные не трогать. Fetch + ff-only merge — без пересечения изменений.
   Перед restart — active generation/operation/activity guard; не прерывать
   генерации и не запускать свои. Доставлять затронутые сервисы, проверять image
   и release. Разные component SHA сами по себе не требуют общего перезапуска.
7. H147 — preview runtime, H148 — anime dedup, H149 — message cache, H150 — usage buckets.
   В `/otchet` публиковать только новый
   согласованный фрагмент, сохраняя прежнюю историю; не заменять публичный JSON
   целиком репозиторным с неопубликованными H134–H136. Следующий ID сверять
   по свежему repo/public JSON. Общий production-smoke требует единого SHA:
   это отдельное ограничение, его не ослаблять ради зелёного статуса.

Подробная история прежних остановок сохранена в Git и verification docs;
она убрана из входного файла, чтобы следующий агент не повторял выполненное.
