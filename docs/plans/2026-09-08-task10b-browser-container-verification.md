# Task 10B — общий список браузерных контейнеров

BASE `4cc95d7fe25f6387ea4fa9daf9e7dfc3de872e7d`. Task10A полностью доставлен:
API/worker/generation-worker `66411078`, web `10c4ef12`, orchestrator `1011a0fd`.
H143 опубликован отдельно: public version 75, 139 старых записей сохранены.

## Граница переноса

В `schemas/project.py` добавлен `CONTAINER_BROWSER_TEMPLATES`:
`("fullstack", "nextjs_entities", "spa", "realtime", "max_miniapp")`.
Четыре прежних определения заменены import aliases с прежними именами:
messages.CONTAINER_NEXT, preview.CONTAINER_NEXT, runtime._CONTAINER_NEXT,
rollback._CONTAINER_NEXT. Значения и порядок неизменны.

| Признак | Что сохраняется |
|---|---|
| Browser containers | Только пять совпадавших значений объединены. |
| Container backend | Прежний orchestrator mapping отдельно включает api/tgbot. |
| MAX/Project Cell | MAX preview early return, Cell runtime bypass и MAX rollback 409 не меняются. |
| База данных | Требование БД задаётся прежним stack registry, а не новым списком. |
| Design/prompt | Отличающиеся наборы, включая исключение realtime из visual templates, не меняются. |

Список не выдаёт права и не заменяет проверки доступа. Source mapping, prompt,
финализация, протоколы, таймауты, данные и зависимости не менялись.
AST всех функций и классов пяти затронутых модулей совпадает с BASE.

## Проверки

До переноса пять SHA256 anchors из artifact map совпали с текущими файлами.
`tests/test_browser_container_contract.py`: **50 passed, 2.88 s** на старом коде.
После переноса вместе с rollback и agent suites: **212 passed, 66.99 s**.
Full Ruff clean; mypy: **276 source files**, ошибок нет.
Первый AFTER вызов тестов содержал несуществующее имя preview suite и не
запустил ни одного теста. Исправленный вызов выше прошёл; product fix для
этой ошибки команды не требовался.

50 новых cases исполняют настоящие preview/runtime/rollback consumers;
подменены только DB/transport/browser границы. Preview проверяется до page.goto,
без заявления о качестве скриншота. Runtime проверяет resync/hot reload,
отрицательные api/tgbot/code пути и Cell bypass. Rollback сохраняет MAX 409
до checkout/runtime mutation. У огромного messages._process_prompt здесь
фиксируется значение списка; сам путь дополнительно покрывают существующие suites.

Команда AFTER из apps/api:

```text
python -m pytest -o addopts='' -q tests/test_browser_container_contract.py tests/test_rollback_container_hot_reload.py tests/test_agent_builder.py tests/test_agent_native.py tests/test_max_generation_contract.py
python -m ruff check .
python -m mypy src
```

Локальные tests DB-free, с dead-loopback DB/Redis. Модель, production DB,
пользовательские генерации, публикация и реальное восстановление не запускались.
Независимое review source/tests/docs/deployment helper: No findings.
Финальный full CI и API-only поставка ещё ожидаются.

## Критерии качества

Один источник значений вместо четырёх; функции, guards и побочные действия
остаются на месте. Нет нового service/package, классов, циклов импортов,
кэша или dependencies. Старые aliases сохраняют внутренних consumers.
Это устранение дублирования, не измеренное ускорение генерации.
Task11 и вся программа рефакторинга ещё не завершены.

## Отдельная корректировка подготовки Docker-теста

Source `f4c35e24737a40624c9f2b7b1015543cd96f356f` pushed. CI34277003261:
orchestrator **1244 passed / 30 skipped / 15 xfailed**. Отдельный live gate:
8 passed / 1 failed — `dd` файла 129 MiB в fixture container завершился с
exit137 до вызова production probe. Тест и orchestrator source этим API
переносом не менялись. Без State.OOMKilled нельзя утверждать доказанный OOM.

В отдельном test-only исправлении fixture helper сохраняет default 64 MiB;
только создание большого файла получает 256 MiB. Размер 129 MiB, проверка
digest, права, запреты и output assertions неизменны. Production probe и
его собственные 256 MiB/64 PID не изменены; новые retry/skip не добавлялись.
Это запас ресурсов подготовки, не доказательство причины прежнего SIGKILL.
Реальный Docker gate должен пройти заново; mock/unit для этого недостаточно.
SDK Docker 7.1.0 зафиксирован lockfile; числовой mem_limit подтверждён в
официальной документации docker-py через Context7. Product source extraction
сохраняется отдельным commit от этой корректировки test fixture.
