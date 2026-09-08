# Task 4 — обновление истории без повторного запроса

## Область изменения

Baseline: `d88ca6febf9e29c2e3119a78295db3decfaeff15` (runtime web source
`179a3b3f321b58400351f39880bea902af681fca`). Между ними в web добавлен только
тест onboarding; production web-код совпадает.

`snapshot.created` обновлял кеш снимков и немедленно запрашивал историю.
Затем эффект `MaxWorkspaceShell`, реагируя на смену HEAD, отменял этот запрос
и запускал такой же. При известной смене HEAD обработчик теперь только
помечает историю устаревшей (`refetchType: "none"`); запросом владеет эффект.
Аналогичный дубль устранён в существующем обработчике успешного rollback.

При том же HEAD или неизвестном предыдущем HEAD прежнее активное обновление
сохранено. Отдельное terminal reconciliation, интервалы polling, все страницы,
выбор старой версии и проверка актуальности истории не изменены. MAX rollback
на сервере по-прежнему запрещён; тест успешного callback не включает эту функцию.

## Измерение и проверки

Реальные QueryClient, Shell и stream hook; управляемые ответы API и события WS.
TanStack Query 5.100.9, React 19.2.5, Vitest 4.1.8.

| Сценарий | До: вызовы / отмены | После: вызовы / отмены |
|---|---:|---:|
| Новый HEAD, одна страница | 2 / 1 | 1 / 0 |
| Новый HEAD, две страницы | 3 / 1 | 2 / 0 |
| Успешный mock rollback, две страницы | 3 / 1 | 2 / 0 |
| Метаданные того же HEAD | 1 / 0 | 1 / 0 |
| Отдельный terminal | 1 / 0 | 1 / 0 |

Это вызовы клиентской API-функции и AbortSignal, а не замер HTTP-сервера или
ускорения генерации. Первоначальная загрузка исключена из событийных счётчиков.

Новый `project-version-refetch.test.tsx`: 13 сценариев. До production-правки
первая группа дала 4 ожидаемых падения / 3 успеха; после — 13/13.
Проверены неизвестный кеш, fallback HEAD, холодная загрузка, неизменный 5s poll,
две страницы, выбор версии, поздний ответ прежнего HEAD и переключение A→B→A.
Холодный запрос без кеша может завершиться до следующего poll с неактуальной
историей; существующий guard сохраняет блокировку до обновления. Это поведение
не менялось и не объявляется устранённым.

Смежный набор: 68 passed / 5 suites. TypeScript, ESLint изменённых файлов и
`git diff --check` прошли. Независимый read-only reviewer проверил потребителей
query key, оба обработчика и тесты: No findings; отдельно 13/13 passed.

Первый полный запуск на системном Node 25.6.1: 64 failed / 408 passed,
7 failed suites; диагностирована несовместимость Web Storage тестовой среды
(`localStorage.clear is not a function`). Production-код и тесты ради неё
не менялись. Повтор на Node 20.20.2 дал **472 passed / 75 suites**;
официальный архив проверен по опубликованному SHA256.

Локальная Next.js-сборка прошла компиляцию, но создание standalone symlink
завершилось Windows `EPERM`. Финальный production build требует Linux CI;
настройки приложения и права Windows ради обхода не менялись.

## Браузер и доставка

Production QA-ссылка открывает MAX регистрацию: авторизованного браузерного
доступа в текущей сессии нет. Изолированная браузерная проверка с transport
fixtures не заменяет live customer-flow. Пользовательскую генерацию не запускать.

Изолированный Edge проверил реальные Shell, ChatPanel, stream hook, preview и
CSS на 1440×1000 и 390×844. Для baseline загружались неизменённые два файла
из `d88ca6fe`; после — текущий исходный код. Новый HEAD: **2 HTTP / 1 отмена →
1 HTTP / 0 отмен** на обеих ширинах. Выбор v31 сохранён после появления v33;
terminal и следующая страница делают по одному запросу; A→B→A и F5 возвращают
актуальную v33. Ошибок консоли и горизонтального overflow нет.

React Profiler сохранён для mount/progress/selection/HEAD/terminal/pagination/F5.
Количество HEAD commits не изменилось: desktop 2, mobile 4. Единичные dev-mode
измерения CPU шумные (22.5→24.3 ms и 22.7→24.5 ms), ускорение render не доказано.
Полные результаты и 8 PNG: `.artifacts/refactor-task4-20260908/browser-report.md`,
`browser-before-results.json`, `browser-after-results.json`. HTTP fixtures не
проверяют серверное завершение генерации: статус тестового запуска после F5
остаётся running. Локальный fixture-сервер остановлен после проверки.

## Доставленный результат

Коммит `10c4ef128006cfedc5e1a781b9dfaa8e1d94b77e` отправлен в `origin/main`.
CI `34262431711`: web typecheck/test/build **success**, image-build **success**,
orchestrator, workflow lint, gateway и syntax **success**. Общий API job на
момент поставки ещё выполнялся; его исходники этим web-пакетом не менялись.
Это не заявление о завершении всего CI run. Ранее тот же API-код прошёл
полный gate на `4960f6b9` (3129 passed / 12 skipped / 8 xfailed).

Production web-only поставка выполнена через документированный compose `full`.
Фактический образ (пересобран и отдельно проверен непосредственно перед rollout):
`sha256:0b65efaeeada358406021164c68710d397e10063d4066d5abffa8b2922dabe70`.
Предварительный staging-образ имел другой image ID, поэтому доказательством
runtime служит именно этот ID плюс label и `/web-health` release `10c4ef12`.

- Изолированный запуск без внешней сети и credentials: `/api/health` = ok.
- Перед обновлением и внутри короткого write gate активных запусков/операций/
  leases не было. После успешной проверки gate снят, helper exit 0.
- Публичные `/login`, `/max/register`, `/max/product`, `/max/guide`: HTTP 200;
  `/max`: ожидаемый 307 на регистрацию. Эти же маршруты проверены до изменения.
- Внешний `/web-health`: status ok, service web, точный `10c4ef12`.
- API `/api/health`: ok, release `4960f6b9`; database/redis/worker/
  generation_worker/deploy_control_plane/preview_storage = ok.
- API/worker/generation-worker и прочие full-контейнеры сохранили image, ID,
  StartedAt; orchestrator сохранил PID/start и `1011a0fd`. Только web перезапущен.
- Пять dirty secondbrain-файлов сохранены побайтно; SHA256 исходного diff
  `0faee2b7c90dfd954d0def658d80cd89f21312061c500b2d9efd6ae06f1d250d`.
- Приватные backups env/nginx/diff и result:
  `/opt/omnia-runtime/releases/task4-web-10c4ef12`. БД/миграции не менялись.

Общий production-smoke workflow всё ещё требует один устаревший release SHA
для разных компонентов; этот контракт не ослаблялся ради зелёного статуса.
Проверены фактические отдельные revisions. Авторизованный live customer-flow
и ускорение старта ячеек этим пакетом не заявляются. Task 3 требует отдельного
решения; Tasks 6–13 не завершены.
