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

## Текущая поставка

- **Task11 message cache ещё не доставлен:** source `bf428456` отправлен в main,
  −32 production строки/−1101 нормализованный UTF-8 байт; 47 до/после, полный
  web502/78, types/lint, browser1440/390 и Astra review прошли. CI34363742655:
  web/image и ещё четыре jobs прошли; результат API job здесь не заявляется.
  После первоначального API502 backend восстановили извне, exact web build/smoke
  прошли. Повторная ручная остановка backend в14:36 UTC сорвала switch: старый
  web2ae восстановлен, nginx/env byte-equal; API502 не позволил подтвердить405.
  Запись retry1/result.json — rolled_back. Инициатор не установлен; до следующей
  попытки явно согласовать обслуживание. H149 остаётся testing и публично
  не опубликован. [Доказательства и точка продолжения](2026-09-09-task11-message-cache-verification.md).
- Ниже — последние **доставленные** component identities. После ручной остановки
  API и оба worker не работают; их прежние успешные health-проверки не описывают
  текущее состояние. Web продолжает работать.
- API/worker/generation-worker `50565efd0c6de2b8c6ee66546d6b4fb16cd05673`, image
  `sha256:47ac83c1f70ee7351812128fcfc56869e8bdd020fe3143ff45207c4fb2cf4664`.
  CI34356863327: все7 jobs success; полная API suite3264 passed/12 skipped/8 xfailed.
  163 теста до/после, wheel/image, Astra review, точные runtime identity,
  health6/6 и публичные bytes/MIME/cache/CORS подтверждены; write gate снят.
- Web `2ae9852010c0df7c12291cdcbb8526f9b97b8771`, image
  `sha256:790e7eb01bc76efa05333230a5acccb9342903c5f9c638d85c23c47960cd3328`.
  Orchestrator `b99af471`. Их identities, остальные IDs/StartedAt и пять dirty
  документов сохранены. Общая compose SHA меняет desired metadata web;
  работающий web остаётся на своём проверенном image/release.
- Запись: `/opt/omnia-runtime/releases/task5-anime-50565efd/result.json`.
  Preview delivery: `/opt/omnia-runtime/releases/task11-preview-2ae98520-retry1/result.json`.

## Следующий шаг

Сначала завершить delivery Task11 message cache: явно согласовать серверное
обслуживание, заново сверить состояние, дождаться/восстановить backend и повторить
подготовку web в новой записи, сохранив обе ошибки. Проверять State.Status и свежий
API health до gate; одна временная зелёная проверка не заменяет координацию.
Затем scoped web deploy,
точные image/release/health и отдельная публикация H149. Другой пакет до этого
не начинать. Task5 extension уже доставлен; его не повторять.

Task3 SQL требует числового контракта: настоящий сериализатор сохраняет
`0.1+0.2` как `0.30000000000000004`, Decimal SUM дал бы `0.3`. Нельзя менять
арифметику/порядок по умолчанию. Отдельный узкий кандидат — одна инициализация
нулевой корзины стадии (−14 строк); он не реализован и не завершает SQL-агрегацию.
Task6/7 — model smoke, который владелец запускает
сам; Task12 — измеренной причины IO задержки. Cleanup/stale-probe usePromptStream
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
7. H147 — preview runtime, H148 — anime dedup, H149 — message cache (пока testing).
   В `/otchet` публиковать только новый
   согласованный фрагмент, сохраняя прежнюю историю; не заменять публичный JSON
   целиком репозиторным с неопубликованными H134–H136. Следующий ID сверять
   по свежему repo/public JSON. Общий production-smoke требует единого SHA:
   это отдельное ограничение, его не ослаблять ради зелёного статуса.

Подробная история прежних остановок сохранена в Git и verification docs;
она убрана из входного файла, чтобы следующий агент не повторял выполненное.
