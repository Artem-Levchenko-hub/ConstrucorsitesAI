# Omnia Agent Kernel v2 — design

**Дата:** 2026-08-15
**Статус:** утверждён владельцем для автономной реализации
**Первый rollout:** MAX Mini Apps, затем остальные container generators

## Цель

Сохранить native-agent как основной интеллект Omnia, но сделать его работу
управляемой, доказательной и экономной. Агент должен быстро создавать полный
продукт, находить точную причину ошибок, не повторять неудачные подходы и не
выдавать сборку без исполнимого доказательства результата.

Superpowers задаёт процесс, Goose даёт совместимый способ выполнять узкие
специализированные подзадачи, а механика OpenClaw используется для долговременной
памяти и compaction. Ни один внешний runtime не становится новым control plane.

## Подтверждённые проблемы

1. План и durable checkpoint существуют, но не отражают фактическое закрытие
   критериев: длинный run может оставить все milestones в `pending`.
2. Anti-loop сравнивает stop reason и точный byte digest. Косметически разные
   правки с одной ошибкой считаются прогрессом.
3. После красной сборки агент не обязан сформулировать гипотезу и минимальный
   эксперимент до следующего изменения.
4. История не хранит граф уже проверенных и опровергнутых подходов.
5. Environment manifest может описывать не те managed exports, которые реально
   выдаются проекту.
6. Финальная ошибка может затереть первичную причину провайдера или runtime.
7. Reconcile исторического MAX snapshot может принять managed root page за
   legacy product и создать рекурсивный runtime.
8. Provider `budget_exceeded` может быть ошибочно повторён как неоднозначный
   платный вызов.

## Архитектура

```text
GenerationRun
  -> Agent Supervisor
       -> Project Brain
       -> Native Agent
       -> Capability Registry / typed tools
       -> optional Goose specialist
       -> Evidence & Release Gate
  -> Snapshot transaction or exact rollback
```

### Agent Supervisor

Детерминированный слой над native loop. Он не пишет продуктовый код и не выбирает
дизайн. Он:

- определяет текущую фазу;
- проверяет разрешённость следующего действия;
- нормализует наблюдения;
- обновляет Project Brain только по фактам инструментов;
- вычисляет семантический прогресс;
- требует diagnosis перед повторным исправлением;
- завершает run только по acceptance evidence.

### Native Agent

Остаётся владельцем задачи и единственным общим reasoning loop. Он получает
компактный `ProjectBrainView`, а не всё накопленное состояние. Native-agent может
читать, писать, собирать, запускать и визуально проверять приложение в текущем
изолированном workspace.

### Goose specialist

Goose не запускает параллельный общий цикл. Supervisor может передать ему одну
ограниченную задачу с JSON Schema результата, например:

- исследовать неизвестную библиотеку;
- предложить минимальный patch для одной compiler error signature;
- подготовить тест для конкретного дефекта;
- выполнить независимый read-only review.

Goose получает тот же capability policy, tenant/workspace scope и deadline.
Если adapter недоступен, native-agent продолжает работу без потери состояния.

## Project Brain

### Приватная память проекта

Хранится внутри `GenerationRun.agent_state` и project-scoped persistence. Поля:

- `objective` — неизменяемая цель;
- `acceptance` — критерии и их evidence/status;
- `phase` — текущая фаза;
- `facts` — подтверждённые наблюдения;
- `hypothesis` — одна активная проверяемая гипотеза;
- `experiments` — изменения и результаты;
- `failed_approaches` — опровергнутые подходы;
- `error_signature` — нормализованная текущая ошибка;
- `artifact_revisions` — изменённые файлы;
- `next_action` — один следующий проверяемый шаг;
- `lessons` — локальные правила проекта;
- `primary_failure` — первая точная terminal-причина;
- `secondary_failures` — последующие proof/reconcile ошибки.

Source code, secrets и скрытое reasoning в Brain не записываются.

### Общая база уроков

Содержит только server-owned технические паттерны без tenant-контента:

- сигнатура класса ошибки;
- runtime/template/version;
- подтверждённая причина;
- безопасная стратегия исправления;
- тест, доказавший исправление;
- confidence и срок актуальности.

Перед записью применяются secret/PII/path redaction и allowlist категорий. Уроки
из одного проекта не могут содержать его prompt, код, данные или идентификаторы.

### Context policy

В каждый LLM turn передаются:

1. неизменяемые system/tool contracts;
2. текущий objective и незакрытые acceptance criteria;
3. активная гипотеза и последний эксперимент;
4. последняя ошибка и один следующий action;
5. top-k релевантных безопасных уроков.

Compaction выполняется на границах фаз, а не по случайному token threshold.

## Superpowers protocol как state machine

```text
DISCOVER -> SPECIFY -> PLAN -> IMPLEMENT -> DIAGNOSE -> VERIFY -> REVIEW -> RELEASE
```

- Fresh build не начинает массовое редактирование до компактного executable plan.
- После красного build/runtime следующий write требует `hypothesis` и ожидаемый
  результат проверки.
- Исправление подтверждается тестом или точным воспроизводимым probe.
- Три неудачных эксперимента одного класса переводят работу в архитектурный
  review, а не в четвёртую случайную правку.
- `done` разрешён только после независимого verification/release contract.

Пользователь не обязан вручную согласовывать внутреннюю спецификацию генерации:
она выводится из принятого brief и хранится как наблюдаемый checkpoint.

## Semantic anti-loop

Прогресс оценивается не только по байтам. `SemanticProgress` учитывает:

- изменился ли normalized error signature;
- закрыт ли acceptance criterion;
- появился ли новый проходящий test/probe;
- изменился ли runtime/visual verdict;
- был ли опровергнут новый hypothesis;
- создан ли ранее отсутствовавший обязательный artifact.

Не являются прогрессом:

- переписывание тех же строк без изменения ошибки;
- повторный read/search неизменённого revision;
- повторный build без source/config/environment delta;
- повторение ранее опровергнутого approach;
- смена формулировки плана без evidence.

При первом повторе Supervisor возвращает уже известный факт и требует другой
эксперимент. При втором — запускает focused diagnosis. При третьем — сохраняет
checkpoint, выполняет architecture review/escalation и запрещает прежний класс
действий до нового evidence.

Это circuit breaker качества, а не финансовый лимит. Он сокращает лишние шаги,
не ограничивая объём полезной работы.

## Typed tools и MCP

Каждое наблюдение нормализуется:

```json
{
  "status": "success|warning|error",
  "summary": "bounded factual summary",
  "root_cause_hint": "optional evidence-based hint",
  "error_signature": "stable semantic signature",
  "next_actions": ["bounded alternatives"],
  "artifacts": ["project-relative paths or proof ids"],
  "evidence": ["test/build/runtime/visual facts"],
  "retry": "never|after_change|transient"
}
```

Environment manifest строится из фактически отрендеренных managed files и
проверяется parity-тестом. Модель не получает API, которого нет в live runtime.

## Error handling

- Первая точная ошибка сохраняется как `primary_failure` и не затирается.
- Verification/reconcile errors добавляются отдельно.
- `budget_exceeded`, auth и validation — terminal provider rejection без повтора.
- Повтор разрешён только для явно trusted transient codes с idempotent turn id.
- Runtime reconcile строит desired tree целиком и переключает его атомарно.
- Managed root page никогда не мигрирует в product entry.

## Безопасность

- Один tenant/project/run на execution workspace.
- Нет root shell, Docker socket, host secrets или соседних проектов.
- Goose наследует те же allow/deny правила; отдельного широкого authority нет.
- Общая память принимает только schema-validated sanitized lessons.
- Любая mutation проходит path, secret, SAST и managed-file policy.
- Publication остаётся единственной snapshot transaction после зелёного proof.

## Rollout

1. Offline replay сохранённых MAX failure traces без вызова живого LLM.
2. Shadow mode: Brain и semantic decisions считаются, но не блокируют native loop.
3. MAX canary: state machine и anti-loop управляют выбранными run.
4. Все MAX run с автоматическим fallback на текущий native behavior.
5. Общий Agent Kernel подключается к остальным container generators.
6. Goose specialist включается только после adapter security/eval gate.

Feature flags позволяют отдельно отключить Brain, semantic gate, global lessons и
Goose adapter. Existing snapshot/release path остаётся rollback anchor.

## Проверка

Обязательные regression fixtures:

1. 69 косметически разных edits с одной TypeScript ошибкой распознаются как loop.
2. Новая error signature считается прогрессом только вместе с новым hypothesis.
3. `trackMaxEvent` невозможен при live manifest без такого export.
4. Managed `page.tsx` не копируется в `ProductApp.tsx`.
5. `budget_exceeded` не повторяется.
6. Primary provider failure не затирается final verification.
7. Checkpoint переживает worker restart и продолжает ровно с `next_action`.
8. Общий урок не содержит tenant text, paths, ids или secrets.
9. Goose unavailable сохраняет успешный native fallback.
10. Green result требует build, runtime, real action/reload и visual evidence.

## Метрики успеха

Сравнение ведётся на одном и том же replay corpus и модели:

- completion rate и pass@1 растут;
- median LLM turns и builds на успешную генерацию снижаются;
- одинаковая error signature не переживает более трёх экспериментов;
- повторные reads/builds без delta стремятся к нулю;
- стоимость измеряется на успешный verified snapshot, не на один вызов;
- ни один красный/пустой/рекурсивный runtime не публикуется;
- tenant-memory leakage равно нулю.

## Не входит в первый кодовый срез

- замена native-agent внешним runtime;
- самостоятельные OpenClaw gateway/channel surfaces;
- общий host shell;
- автоматическое обучение модели на клиентском коде;
- multi-agent swarm без ограниченной роли и schema результата.

Эти ограничения не уменьшают функциональность продукта: они предотвращают
появление второго неконтролируемого agent loop и сохраняют multi-tenant boundary.
