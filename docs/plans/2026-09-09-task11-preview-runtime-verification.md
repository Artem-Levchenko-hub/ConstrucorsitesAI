# Task11 — управление runtime предпросмотра

BASE `9d6a361b76c5710a46b411f4fa51ef2eae6124bd`. Отдельный R-пакет после
доставленного переноса WebSocket transport. Реализация, проверка и независимое
ревью — GPT-6 Astra. Пользовательские генерации не запускались.

## Граница и карта кода

Из `apps/web/src/components/workspace/PreviewFrame.tsx` вынесено управление
runtime в `apps/web/src/hooks/usePreviewRuntime.ts`: запрос текущего состояния,
опрос до running/failed, однократный автоматический запуск, ручной повтор и
обновление общего query cache. Компонент получает runtime/state/starting/start;
отображение, iframe, инспектор, история и остальные эффекты остаются в нём.

Сохранены ключ `["runtime", projectId]`, enabled для fullstack и текущей версии,
retry=false, интервал 2000 ms, порядок эффектов, mount-scoped autoStarted,
публикация ответа start в тот же cache. Существующая семантика смены projectId
и поздней mutation не исправлялась. Известные cleanup/stale-probe gaps в
usePromptStream также не входят в этот пакет.

PreviewFrame diff: +6/−52 строки. Новый hook: 65 строк. Это отделение обязанности,
а не уменьшение общего объёма кода или доказанное ускорение запуска приложения.
API, зависимости, lockfiles, схемы, тексты, дизайн и интервалы не меняются.

## Проверки до и после

- Исходные consumer suites: 66/66, 5 файлов.
- Новые 8 characterization tests выполнены на настоящем PreviewFrame до переноса
  и после него; вместе с исходными — 74/74 после.
- Проверены delayed read → start → shared cache → iframe/load, polling до
  running/failed, initial error/manual retry, пауза на старой версии,
  static→fullstack cached project, поздний ответ прежнего project и unmount.
- Пробная подмена 2000 ms обнаруживается тестом. Постоянный код не менялся этой
  проверкой. API seams подставлены; используется настоящий React Query observer.
- В unit fixture убрано только удержание уходящих AnimatePresence-элементов
  ради fake clock. Реальная анимация проверена отдельным browser fixture.
- Focused ESLint и typecheck прошли. Полная web suite на Node20.20.2:
  496 passed / 78 файлов, 119.60s, exit0. Отдельно новые8 повторены на Node20.
  Linux build фиксируется в разделе доставки после завершения.

Первый полный запуск на локальном Node25 остановлен после известных ошибок
Web Storage в незатронутых billing/MAX тестах. Assertions не ослаблялись;
поддерживаемая среда проекта — Node20 и pnpm9.15.0 по текущему CI.

## Браузер

Edge headless, настоящий PreviewFrame, CSS, Framer Motion и Query; HTTP/runtime
fixture, 1440×900 и 390×900. До/после: один POST start, четыре GET runtime
(включая возврат к HEAD), один и тот же порядок маршрутов и сообщений.
Проверены provisioning, live iframe/load skeleton, ready/state/pick инспектора,
image-only история без polling, возврат к HEAD и отсутствие polling после
unmount. Ошибки страницы/console и горизонтальное переполнение отсутствуют.
Скриншоты live preview совпадают побайтно. Это браузерное доказательство на
fixture; реальный запуск Docker runtime и авторизованный customer-flow не заявляются.

Raw evidence: `.artifacts/task11-preview/` в рабочей копии; committed тест:
`apps/web/src/lib/__tests__/preview-runtime-lifecycle.test.tsx`.

Команды из `apps/web`: `pnpm exec vitest run
src/lib/__tests__/preview-runtime-lifecycle.test.tsx`; полный gate
`pnpm --package=node@20 dlx node node_modules/vitest/vitest.mjs run`;
`pnpm exec tsc --noEmit`; `pnpm exec eslint src/hooks/usePreviewRuntime.ts
src/components/workspace/PreviewFrame.tsx
src/lib/__tests__/preview-runtime-lifecycle.test.tsx`.

## Ревью и качество

Независимый Astra reviewer проверил исходный diff и все восемь тестов: No findings.
Порядок hooks/effects сохранён; дополнительный useQueryClient читает тот же
контекст. Имя hook отражает одну обязанность, side effects видны внутри него,
нет generic context object или переключателей транспорта. Тесты проверяют
реального потребителя; карта входа и команды находятся в этом документе.

Сценарий выкладки прошёл отдельное review: закрыты замечания к обработке
ошибок переключения, проверке immutable image ID, сохранению исходного backup
при повторе и удержанию write gate. После открытия write gate ошибки отчётного
readback не запускают незаблокированный rollback. Bash syntax check прошёл.

## Доставка

Source `2ae9852010c0df7c12291cdcbb8526f9b97b8771` committed/pushed в origin/main
и доставлен web-only. CI34353326482: web и image-build success; также прошли
orchestrator/gateway/синтаксис/workflow. API job на момент этой записи ещё
выполнялся; API/оркестратор/gateway/workflow source не менялся относительно
BASE, полный CI34349089108 на BASE success. Новый полный API gate здесь не
заявляется пройденным до его фактического результата.

Итоговый образ: `sha256:790e7eb01bc76efa05333230a5acccb9342903c5f9c638d85c23c47960cd3328`.
Linux production build и изолированный запуск без сети/секретов с read-only
файловой системой прошли. Running container image ID совпал с проверенным
образом; локальный и публичный `/web-health` подтверждают точный source SHA,
контейнер healthy. `/login`, `/max/register`, `/max/product`, `/max/guide` —200;
`/max` —прежний307. `/api/health`: все6 checks ok, прежние component SHAs.

Первая попытка остановилась до env/web изменения: мгновенный probe после
nginx reload ещё не увидел503. Nginx восстановлен, прежний web ID сохранился.
Повтор с отдельной записью подтвердил reload convergence: probe1=405,
probe2=503; generation/operation/activity counts=0. Только web пересоздан.
После снятия gate helper завершился exit1 на мгновенном финальном readback;
повторная независимая проверка подтвердила POST `/api/health`=405,
восстановленные bytes nginx, правильные image/SHA/health и неизменность других
container IDs/StartedAt, orchestrator PID и dirty secondbrain diff.
Эта проверка закрыла доставку без ещё одного перезапуска или rollback.

Запись итоговой поставки:
`/opt/omnia-runtime/releases/task11-preview-2ae98520-retry1/result.json`.
Первая запись сохранена рядом без перезаписи. Gate снят; новый model/customer
flow не запускался. Публичный H147 обновляется отдельно от repo JSON, сохраняя
прежнюю публичную историю и не раскрывая ранее неопубликованные записи.

Исходный runtime: API/worker/generation-worker `85593d30`, web `c03dd088`,
orchestrator `b99af471`. Серверный checkout и origin/main — BASE; последний
коммит BASE содержит operations docs. Пять dirty secondbrain документов,
env/allowlists и незатронутые контейнеры сохраняются.

Штатный compose использует общую OMNIA_RELEASE_SHA: при web-only поставке
меняется её desired metadata также для API/workers, но они не пересоздаются
и сохраняют прежние runtime env/ID. Это различие не выдаётся за неизменность
всей желаемой compose-конфигурации.

## Следующая граница

Владелец уточнил приоритет: меньше рабочего кода и контекста для агентов.
Этот extraction дал +19 production строк; следующий простой перенос
envelope→AgentStep отложен. Дальше выбирать доказанную дедупликацию или удаление
ненужной прослойки с измеримым сокращением, сохраняя контракт. Task3 числовой контракт, Task6/7 model
smoke и Task12 измеренная причина IO задержки остаются отдельными условиями;
полная матрица20 и оценка10/10 этим переносом не закрываются.
