# P01 — PostgreSQL metadata probe: проверка и production-доставка

## Итог

P01 доставлен 8 сентября 2026. Код: 1011a0fdf7cc4f636e7550937c0e89447fc21bcd. Отчёт: 303274b29c377ce40a1d302eb4f686b5c102454a. Разница второго коммита — только otchet/data.json; runtime-source относительно проверенного кода не менялся.

Полный [CI 34235756123](https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI/actions/runs/34235756123) завершён success; статус и headSha повторно прочитаны при подготовке передачи. Host orchestrator обновлён в 17:19:18 MSK, release SHA 1011a0fd. Остальные сервисы не пересобирались.

После явной команды владельца вся дальнейшая работа — исключительно primary GPT-6 Astra. Более ранние правки Sol сохранены и пересмотрены; его работа остановлена. Отдельный Astra reviewer не смог запуститься из-за agent thread limit. Независимое второе ревью НЕ заявляется.

## Что изменено

В DockerCellResourceManager._ensure_postgres_initialized только решение «есть ли данные» больше не вызывает полный read_volume_files/export/tar тома PostgreSQL. Новый probe_postgres_volume_after_legacy_cleanup возвращает наличие обычного файла после точной прежней очистки служебного пароля.

- Любой regular file означает nonempty/skip init, а не проверенную исправность PostgreSQL.
- Пусто — прежняя последовательность init/secret staging.
- Missing identity, ошибка, timeout, malformed/ambiguous output — отказ, а не повторная инициализация.
- Legacy cleanup затрагивает только прежний regular PGDATA/postgres-password.txt; symlink PGDATA/legacy path отвергается.
- Ошибка find не трактуется как пустой том.
- Helper считывает UID:GID корня и делает фиксированный scan от числового владельца тома. Это позволяет читать реальные postgres-owned 0700/0600 без дополнительных capabilities или изменения прав/владельца.
- Networkless helper isolation, no-new-privileges, fencing/discovery и cleanup сохранены.
- Настоящие snapshot/restore archives, версии, пользовательские данные, MAX-шаблоны, API и frontend не менялись.

Изменённые runtime-файлы:

- apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py
- apps/orchestrator/src/omnia_orchestrator/services/docker_py_cell_backend.py

Регрессии: tests/_cell_fakes.py, test_docker_cell_resources.py, test_docker_py_cell_backend.py, новый test_live_postgres_volume_probe.py; изолированный gate в .github/workflows/ci.yml. Остальное — отчёт.

## Найденные и устранённые review-проблемы

1. Symlink родительского PGDATA мог перенаправить удаление на чужой файл: теперь отклоняется до traversal, target hashes проверены на настоящем Docker.
2. find в command substitution мог скрыть собственный отказ: explicit status handling и реальный unreadable-directory fixture.
3. Root helper с cap_drop ALL не мог читать postgres-owned 0700: probe выполняется с реальным числовым owner без расширения прав.
4. Benchmark терял вывод через capsys: метрики теперь доступны в CI logs.
5. Тесты, симулировавшие filesystem-алгоритм или проверявшие текст скрипта, заменены/сужены: реальные filesystem-свойства проверяет Docker gate.

## Тесты

Baseline до изменения: 62 focused tests passed за 4.75 с. RED воспроизведён в реальном manager на старом вызове read_volume_files с AssertionError("whole-volume read forbidden"), затем GREEN.

Финальный локальный запуск из apps/orchestrator:

~~~powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q
.venv/Scripts/python.exe -m mypy src
~~~

- 1248 passed / 26 skipped / 15 xfailed за 137.97 с.
- Mypy: 72 source files passed.
- Ruff всех изменённых source/tests: passed.
- CI YAML parse, report JSON/unique IDs и git diff --check: passed.

Первый локальный run без -X utf8: 1243 passed / 6 failed / 25 skipped / 15 xfailed. Шесть неизменённых nginx fixtures писали cp1251, читались UTF-8. Все шесть прошли с UTF-8 без изменения nginx-кода/тестов. Локальная среда: Python 3.14.3, Windows cp1251. Запуск mypy из корня однажды дал 14 посторонних diagnostics из-за другого configuration context; правильный повтор из apps/orchestrator прошёл без правок.

Linux orchestrator CI job 102092893877:

- Full suite: 1244 passed / 30 skipped / 15 xfailed за 113.94 с.
- Отдельный real-Docker gate: все 9 сценариев прошли за 7.62 с.
- Весь CI, включая API gate/MAX starter, web, gateway и сборки, завершён success.

Skip интеграционного теста локально не выдаётся за Docker-проверку. Настоящий Docker запускался в изолированном Ubuntu CI, не на production.

## Реальная проверка Docker и измерения

Использован production-pinned helper image:

~~~text
alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
~~~

Сценарии: empty/legacy-only; small/16 MiB/129 MiB и hashes; числовой postgres owner UID 70 с 0700/0600; inaccessible directory; exact legacy symlink; два parent symlink targets; symlink-only и FIFO-only. Helper cleanup проверен.

| Измерение в CI | Результат |
|---|---|
| Прежний успешный read 16 MiB | 0.160410 с |
| Новый probe того же 16 MiB | 0.202784 с |
| Новый small probe | 0.207136 с |
| Новый probe 129 MiB | 0.206210 с |
| Прежний regular-file payload | 16 777 216 байт |
| Новый owner + decision output | 13 байт, без HTTP/Docker metadata |

SHA256 129 MiB fixture до/после совпал: 9efc5dc2d6584895ba90dc7a5ab20477e9de4a6cd0195c13c502784cb41d5066.

**На малом томе ускорение времени не доказано: новый probe в этой выборке примерно на 42 мс медленнее.** Это ограниченный диагностический замер, не серия для статистического утверждения о скорости. Доказаны устранение выгрузки содержимого/роста памяти и обхода всего файла, а также снятие 128 MiB лимита старого решения о наличии данных. Время полной генерации не измерялось.

Production source diff P01: 115 added / 9 removed, net +106 строк. P01 — IO/безопасность решения, не уменьшение числа строк всей программы. Task 5 выбран отдельно для source deduplication.

## Production

Документированный host service: omnia-orchestrator.service, cwd /opt/omnia/apps/orchestrator, env .env, uvicorn из его .venv. /opt/omnia-runtime/.env.orchestrator не использовался и не менялся.

Перед restart дважды read-only проверены 0 active generation_runs, project_cell_operations и project_cell_activity_leases. Restart только orchestrator. Первые краткие curl connection-refused во время старта сменились точным healthy response.

Проверено после запуска:

- systemd active/running; /health status ok и release_sha 1011a0fd;
- публичный /api/health: all checks ok, dependency orchestrator SHA 1011a0fd;
- web health ok с SHA 179a3b3f;
- существующий MAX canary health ok; новая пользовательская генерация не запускалась;
- остальные production-контейнеры сохранили start times.

Server был detached на 179a3b3f при более старом local main. Без изменения source/diff привязан к codex/production-p01-20260908 с upstream origin/main, затем ff-only до code/report revisions. Пять unrelated secondbrain edits hash-checked и сохранены.

В фактическом env изменена только release label. Всё остальное hash-checked без раскрытия содержимого. Закрытая резервная копия: /tmp/omnia-p01-env-1011a0fd.zLWqRd/orchestrator.env. Это временный путь; не считать его гарантированно существующим при будущем recovery.

Global production-smoke уже до P01 ожидал устаревший SHA bd3f499c268ff840634b0778143cc0ebcacd84e7. Scoped endpoint health не означает, что этот общий exact-SHA monitor зелёный. Автоматически ослаблять его либо менять все service labels запрещено.

## Публичный отчёт

Репозиторный отчёт после P01 — version 75. Public /otchet получил только H137, соответствующий latest item и V4 step: version 68 → 69, 133 → 134 гипотезы. Старые записи сохранены; ранее не опубликованные H134–H136 остались закрыты. HTTP readback подтвердил точное H137 equality.

Предыдущий public JSON сохранён в /tmp/omnia-p01-report-303274b2.JKyDIC/before.json; это временный recoverable файл, его наличие нужно проверять заново. Для следующей записи не публиковать целиком репозиторный data.json.

## Что P01 не доказывает

Не доказано ускорение/успешное завершение каждой полной генерации, не завершён общий рефакторинг, не выполнен Task 5, не разработан новый rollback. Отдельное независимое Astra review недоступно. Эти ограничения явно сообщены владельцу и переданы следующему агенту.
