# Task11 — отдельный транспорт потока генерации

BASE `eada56a2e3836e43ade8cebfdeb60581008231d9`.
Task10B полностью доставлен: API/worker/generation-worker `85593d30`, web
`10c4ef12`, orchestrator `1011a0fd`. H144 опубликован: public version76,
140 предыдущих записей сохранены, HTTP readback прошёл.

## Граница изменения

Из `hooks/usePromptStream.ts` в `lib/prompt-stream-transport.ts` перенесены
`StreamHandle` и `openRealStream`; в hook добавлен import. Целиком сохранены
тело hook, транспорт, комментарии и порядок побочных действий. У функции
добавлен только export. Новый модуль импортирует только тип `WsEvent`.
WebSocket, window и public env читаются при вызове, не при загрузке модуля.

Hook diff: +1/−86 строк. Сохранены heartbeat25s, replay query/watermark,
JSON/control frames, cancel/onclose, callbacks и обработка ошибок.
Применение событий, reconnect/watchdog, project refs, выбор истории и
preview lifecycle остаются в прежних местах. UI, API, зависимости,
конфигурация, polling и протокол не меняются.

Этот пакет отделяет управление соединением для чтения и проверки одним
модулем. Он не заменяет весь lifecycle hook и не обещает runtime speedup.
Предсуществующие unmount/stale-probe cleanup gaps не исправлялись:
это отдельное изменение поведения. Их характеристические assertions
остаются только во временном BEFORE/AFTER fixture, не в постоянных тестах.

## Локальные доказательства

| Проверка | Результат |
|---|---|
| BEFORE: actual AST transport14 + real hook5 | 19 passed, 27.54s |
| AFTER: те же assertions, только path/export adapter | 19 passed, 4.15s |
| Новые direct-module/real-hook regression tests | 15 passed, 3.06s |
| Полный web suite, Node20 | 487 passed / 77 files, 40.48s |
| ESLint четырёх файлов и `tsc --noEmit` | Passed |
| Независимый source/helper review | No findings |

BEFORE/AFTER время unit suite не сравнивает производительность приложения.
Permanent tests проверяют настоящий новый модуль/React hook через fake native
WebSocket и HTTP seams; они не воспроизводят серверный handshake/auth.

После git apply обнаружено изменение только сериализации EOL: прежний Git
blob hook содержал смешанные LF/CRLF, рабочий файл стал целиком CRLF. Для
чистого diff восстановлены точные исходные окончания всех неизменённых
строк. Тело hook теперь совпадает с BASE побайтово. Нормализованный код,
на котором прошли unit/browser проверки, неизменен: hook SHA256
`d2eb4bec5e87e923bf3ca49efa42e4df6e915f30511b08f8593a7bc61149eed2`,
transport SHA256 `b31734faeff13114586c0c4d10317be3af8aebbd6205b84dcb7ea81525af097d`.
Финальный EOL diff проверен повторно независимым reviewer; diff check чистый.

Локальный Next build с итоговыми файлами скомпилирован за18.9s, проверил
типы и создал46 static pages. Standalone packaging завершился
`EPERM: operation not permitted, symlink` на Windows, как в Task4.
Это не успешная полная сборка. Права Windows и настройки Next не менялись;
для поставки обязательны Linux web CI и production image build.

## Браузер до/после

Edge1440×1000 и390×844, одинаковые assertions/fixtures, реальный Shell,
ChatPanel, hook, новый transport и CSS. AFTER runner меняет только имя
выходных artifacts; исходный BEFORE сохранён.

- F5 подключает активный run с `after_seq=0`.
- После разрыва reconnect использует watermark7: BEFORE1013/1015ms,
  AFTER1015/1008ms. Это шум планировщика, не ускорение.
- Выбранная v31 сохраняется при reconnect, новом HEAD и terminal.
- Terminal→F5 читает completed fixture state и не открывает новый socket.
- Console/page errors0; горизонтального overflow нет, layout совпадает.

Фактические URL, socket states, send records и acceptance flags равны.
Локальный fixture server остановлен, порт4312 освобождён. Реальные cookies,
WS server replay, пользовательская бизнес-БД и модельные генерации не
проверялись. Artifact: `.artifacts/refactor-task11-20260908/browser-after-report.md`.

## Linux-проверки и поставка

Source `ff4421549c05c1022665cce3b1d4130b694f2b0e` committed/pushed в origin/main.
CI34282672170: web typecheck, **487 tests / 77 files, 58.48s**, полная Linux
сборка и production image-build **success**. Orchestrator:1244 passed /
30 skipped / 15 xfailed, отдельно9 Docker tests. На момент web-only поставки
API job ещё выполнялся. API/orchestrator/gateway/workflow source побайтово
не менялся с85593d30; предыдущий полный API gate3264 прошёл на том же коде.
Локальный Windows EPERM не выдаётся за успешную локальную упаковку.

Финальная проверка CI34282672170: все jobs **success**, последний API job
завершился 2026-09-08 22:11:13 UTC. Полный API: **3264 passed / 12 skipped /
8 xfailed, 779.01s**; отдельные exact-edit26, artifacts41 и cancellation20
также прошли. Это результат текущего source SHA, полученный после web rollout.

Production web работает на `ff442154`, image
`sha256:279e096fa8c5768e7ad14a7057d88d88c6354dfffc167722fde5936e36e0c748`.
Образ построен из точного Git archive с production public build args.
До переключения прошёл isolated web health: network none/read-only.
Active generation/operation/activity gate был0; перезапущен только web.
Release/health и `/login`, `/max/register`, `/max/product`, `/max/guide`200,
прежний `/max`307 подтверждены. Write gate снят; POST `/api/health`405.

API/оба workers `85593d30`, orchestrator `1011a0fd` и остальные процессы
сохранили image/process/env identity. Пять dirty документов сохранили hash
`0faee2b7c90dfd954d0def658d80cd89f21312061c500b2d9efd6ae06f1d250d`.
Запись: `/opt/omnia-runtime/releases/task11-web-ff442154`.
Независимое cumulative review всех7 web-файлов `179a3b3f..ff442154`:
No findings. Это дополняет отдельное review переноса и deployment helper.

Task11 transport package доставлен. API, пользовательские данные и ячейки
этим пакетом не менялись; новых model/customer генераций не запускали.
H145 подготовлен для отдельной публикации с сохранением публичной истории.
Полная программа и все20 end-to-end сценариев не объявляются завершёнными:
[матрица фактических проверок](2026-09-09-refactoring-scope-review.md).
