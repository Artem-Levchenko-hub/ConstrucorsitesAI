# Продолжение рефакторинга, 13 сентября 2026

Владелец поручил продолжить прежний план и убрать остаточные дубли.
BASE: `deddd35bd41f9f424b52226b3e8bde5e8423b770`; локальный checkout,
`origin/main` и production совпали. Пять сторонних серверных документов сохранены.

## Сверка плана

Ранее доставлены Task2/P01, Task4, Task5 и anime extension, Task8,
Task9 cancellation, Task10A/10B, Task11 transport/preview/message cache,
Task3 usage buckets. Их реализации присутствуют; повторное выполнение не нужно.
Доказательства и ограничения прежних поставок остаются в handoff и scope review.

Task3 SQL SUM не эквивалентен текущему последовательному сложению float.
Task6/7 меняют модельный ввод и требуют отдельной проверки результата генерации
владельцем. Task12 пока не имеет подтверждённой причины задержки: повторные
проверки после Docker effects нельзя удалять по одному внешнему сходству.
Task13 не закрывает всю матрицу пользовательских сценариев автоматически.
Упоминание MAX restore409 в старом плане историческое: последующие поставки
адаптивного восстановления определяют актуальное поведение, которое сохраняем.

## Пакет 1: неиспользуемый сборщик catalog

Тип R/Task13. Единственный изменённый production файл —
`apps/api/src/omnia_api/services/prompt_builder.py`.
Удалены `_CATALOG_SYSTEM_PROMPT`, `_build_catalog_system_prompt`,
`_build_catalog_messages` и устаревшее описание совместимого shim.
Действующий владелец catalog instructions — `services/lean_prompt.py`;
его вызывают `build_messages` и router messages. Новых слоёв нет.

Поиск всех tracked consumers нашёл только определения, внутренние вызовы
удалённого блока и исторические документы. Общие `_compute_skill_brief`,
`_format_selection_block` и `HISTORY_LIMIT` остались у прежнего владельца.
Рабочие инструкции, маршрутизация и пользовательские строки не менялись.
Net production source: **−95 строк**; tests/fixtures считаются отдельно.

До удаления заморожены SHA256 полных сериализованных payload реального
`build_messages`: catalog/freeform/plain/edit × шесть template IDs × RU/EN.
Текущий mixed-language запрос, история, выделение и память проекта входят в fixture.
Skill lookup отключён в fixture для детерминизма; это не модельная приёмка.
48 payload совпадают до/после; вместе со смежными suites **160 passed**.
Первая попытка baseline без обязательных test env дала четыре setup failures;
повтор с синтетическими DATABASE_URL/JWT_SECRET прошёл до изменения production.
Никаких подключений к production DB или модельных генераций не было.
Полные API Ruff и mypy (283 файла) прошли.

Команда focused gate из `apps/api`:

```sh
uv run --frozen python -X utf8 -m pytest -o addopts='' -q tests/test_prompt_routing_baseline.py tests/test_prompt_builder.py tests/test_lean_prompt.py tests/test_generation_mode.py tests/test_prompt_language.py
```

Review, полный CI и доставка фиксируются после фактического завершения.
Откат кода — обычный revert этого пакета; данные и схема не изменяются.

## Следующие подтверждённые кандидаты

1. Task9: два одинаковых блока progress-start → legacy provisioning → progress-ready
   в `messages.py`. Сохранить внешние gates, исключения и установку ready только
   после успеха; проверить оба потребителя и отложенный повторный вызов.
2. Task9: три одинаковых чтения MAX config и рендера starter files в `messages.py`.
   Общий helper должен заново читать config при каждом вызове; rollback и ошибки
   остаются у callers. Кэширование не входит в пакет.

Объединение exception cleanup в orchestrator даёт лишь шесть строк сокращения;
дополнительная поставка сейчас не приоритетнее двух кандидатов API.
