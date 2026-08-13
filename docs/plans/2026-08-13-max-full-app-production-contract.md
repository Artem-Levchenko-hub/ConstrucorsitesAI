# MAX full-app production contract

## Почему потребовалось изменение

MAX работал в том же native agent loop, что container-приложения, но production-вызов
включал отдельный preview-first профиль: урезанный toolset без planning, skills и `see`,
минимальный `_reference_max_completion_gap` и BuildPlan только для не-MAX проектов.
Поэтому один большой `ProductApp.tsx` с локальным состоянием мог пройти build и получить
статус done, даже если действия не восстанавливались после reload, ошибки подавлялись, а
визуальная и функциональная проверка не запускались.

## Единый pipeline

MAX теперь переиспользует общие механизмы container generator:

1. Shared `BuildPlan` строит screens/entities/capabilities и сохраняется в
   `projects.discovery_spec`.
2. Native agent создаёт observable plan через `plan_task`, фиксирует milestones через
   `update_plan` и не может завершиться с pending/blocked step.
3. Server-owned skill index даёт обязательные product/design/production packs и
   post-render `visual-evaluation`; read-only MCP остаётся allow-listed.
4. Agent пишет только разрешённые browser product files. MAX executor по-прежнему
   блокирует locked runtime, API routes, direct DB, server imports и секреты.
5. `max_completion_gap` проверяет art direction, brief coverage, verified MAX identity,
   managed AI/integrations, truthful data, awaited writes, mounted reload restore,
   async states, legal navigation и BuildPlan markers.
6. После clean build выполняются `runtime_check` и signed `see`. Тот же signed session
   запускает browser functional gate: main navigation, primary action, persisted write +
   reload read, console/network errors, 390px overflow и базовую accessibility.
7. Независимый release proof повторяет signed functional gate на точном финальном дереве.
   Красный verdict восстанавливает предыдущий snapshot/checkpoint и публикует ноль файлов.
8. Положительная signed attestation записывается в одной DB-транзакции со snapshot;
   MAX snapshot без proof не существует.

## Детерминированные hooks

Свободный UI остаётся свободным по композиции и компонентам. Для исполняемого proof
агент ставит инертные hooks на реальные семантические элементы:

- `data-omnia-screen-nav` — переключатель главного view;
- `data-omnia-screen="<planned route>"` — отрисованный view;
- `data-omnia-primary-action` — основное полезное действие;
- `data-omnia-persisted-action` — managed user-data write;
- `data-omnia-capability="<plan id>"` — control конкретной capability.

Hooks не заменяют `button`/`a`, `aria-label`, видимый heading или реальное поведение.

## Fail-closed и bounded recovery

- Generic `probe`/`verify_isolation` не выдаются MAX: они требуют web email-auth и не
  доказывают signed initData runtime.
- Signed visual/functional proof считается зелёным только при реальном результате;
  unavailable infrastructure не становится pass.
- Компиляторные и визуальные repair loops остаются ограниченными существующими turn,
  wall-clock, reconnect и visual-repair пределами.
- Обычные container stacks не меняют toolset, prompts или release decision.

## Локальная проверка

- 194 целевых теста: MAX source contract, native lifecycle, shared BuildPlan,
  observable plan, signed functional evaluator и release proof.
- Ruff по 14 затронутым Python-файлам.
- mypy по семи затронутым runtime-модулям.

До production-оценки остаётся fresh canary: сгенерировать приложение с пользовательской
историей, пройти все main views, выполнить marked action, обновить страницу, подтвердить
восстановление данных, visual score и `overall_passed` attestation exact commit.
