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
| Task4 | Убраны повторные запросы истории. [Проверка](2026-09-08-task4-history-verification.md). |
| Task8 | Общая SQL-подготовка artifacts, прежняя транзакция. [Проверка](2026-09-08-task8-artifacts-verification.md). |
| Task9 | Cancellation helper в существующем service. [Проверка](2026-09-08-task9-cancellation-verification.md). |
| Task10A/10B | Exact-edit validator и browser-container список. [10A](2026-09-08-task10-exact-edit-verification.md), [10B](2026-09-08-task10b-browser-container-verification.md). |
| Task11 transport | Отдельный WebSocket transport. [Проверка](2026-09-09-task11-transport-verification.md). |
| Task11 preview runtime | usePreviewRuntime, web496/78, focused74, browser1440/390, Astra No findings. [Проверка и доставка](2026-09-09-task11-preview-runtime-verification.md). |

## Текущая поставка

- Web source `2ae9852010c0df7c12291cdcbb8526f9b97b8771`, image
  `sha256:790e7eb01bc76efa05333230a5acccb9342903c5f9c638d85c23c47960cd3328`.
  Exact release/healthy/routes200/307 подтверждены, write gate снят.
- API/worker/generation-worker `85593d30`, orchestrator `b99af471`.
  Остальные IDs/StartedAt, orchestrator PID и dirty документы сохранены.
- CI34353326482: web/image-build/orchestrator/gateway/syntax/workflow success;
  API job отменён следующим docs push. Backend source не менялся;
  полный BASE CI34349089108 success. Отменённый job не считать пройденным.
- Запись: `/opt/omnia-runtime/releases/task11-preview-2ae98520-retry1/result.json`.
  Две задержки readback после nginx reload разобраны в verification doc;
  итог подтверждён отдельно, без скрытого неудачного deployment.

## Следующий шаг

Task5 extension реализован: четыре одинаковых `anime.min.js` заменены одной
копией через существующий materializer. Итог −52 181 source bytes/−25 строк;
163 теста до/после, lint/type и установленный wheel прошли. [Проверки](2026-09-09-task5-anime-verification.md).
Независимое Astra review: No findings. Завершить commit/push, полный API CI и поставку API/workers;
до закрытия delivery loop не начинать другой пакет. Нужен тот же результат
golden/init/export/rollback/image, без новых прослоек и изменений поведения.

Task3 требует числового контракта; Task6/7 — model smoke, который владелец запускает
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
7. H147 относится к preview runtime. В `/otchet` публиковать только новый
   согласованный фрагмент, сохраняя прежнюю историю; не заменять публичный JSON
   целиком репозиторным с неопубликованными H134–H136. Следующий ID сверять
   по свежему repo/public JSON. Общий production-smoke требует единого SHA:
   это отдельное ограничение, его не ослаблять ради зелёного статуса.

Подробная история прежних остановок сохранена в Git и verification docs;
она убрана из входного файла, чтобы следующий агент не повторял выполненное.
