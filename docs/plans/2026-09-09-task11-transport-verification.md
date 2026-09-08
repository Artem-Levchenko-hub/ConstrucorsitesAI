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

## Оставшаяся поставка

Нужны commit/push, Linux web/image gates и web-only deployment точного SHA.
Подготовленный helper проверяет исходный web10c4/API85593d30, active-work
gate, точные images/env, остальные процессы и пять dirty документов.
Откат самой поставки возвращает старый web image без изменения данных.
Task11 transport package пока не объявляется доставленным; полная
программа и все20 end-to-end сценариев также не объявляются завершёнными.
