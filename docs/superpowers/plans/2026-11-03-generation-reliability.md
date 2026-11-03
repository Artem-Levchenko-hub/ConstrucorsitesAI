# Generation reliability Implementation Plan

> **For agentic workers:** Use bounded implementation ownership and independent read-only review. Root owns integration, Git and deployment. Steps use checkbox syntax. User granted autonomous technical decisions; do not insert another approval gate.

**Goal:** Доставить обязательный контракт SDK и свежий compiler repair context до каждого поддерживаемого model request, затем доказать рабочий пользовательский путь.

**Architecture:** После выбора provider добавляется единый managed SDK block. Общий FAST_CHECK использует авторитетную source_failure классификацию; bounded renderer одинаково передаёт свежую диагностику обоим протоколам, включая early check. Действующая финальная приёмка остаётся обязательной.

**Tech Stack:** Существующие Python/FastAPI pipeline, TypeScript/Next.js SDK, pytest и действующие controller receipts. Без новых библиотек по умолчанию.

**Spec:** docs/superpowers/specs/2026-11-03-generation-reliability-design.md

## Global Constraints

- Base `884cfeec797488eaa10f89cbbd07abdafe89fe01`; isolated worktree, branch `codex/generation-reliability-20261103`, upstream `origin/main`.
- Все агенты — GPT-6.1 Sol, reasoning xhigh. Astra запрещена. Root serial owner Git/deploy; workers не commit/push/deploy.
- Сохранять чужую dirty работу в старом checkout и существующие данные приложений.
- Не менять admission, полномочия, protected SDK, квоты, биллинг, модель или бюджеты без доказанной необходимости.
- Секреты и реальные config literals не попадают в диагностический packet/логи.
- Source context не становится progress receipt; green относится только к проверенному источнику.

## Review Focus

1. Portable replacement и native early build не должны терять contract/feedback из-за иной ветки или UI truncation.
2. Stale/mismatched receipt и infrastructure failure с похожим TS-текстом не дают source repair context.
3. Последовательные red builds используют новый snapshot; no-op/read не обновляют allowances или старый green.
4. Конфигурационные literals/секреты и чрезмерные данные не проникают в packet; неполнота обозначена честно.
5. Native/legacy recovery сохраняют идентичность уже закреплённого provider request и обязательную финальную приёмку.

## Task 1: Mandatory SDK contract after provider selection

Owner: sdk_contract_worker.

Files: `apps/api/src/yleum_api/services/max_project_kit.py`, `max_data_evolution.py`; связанные tests `test_max_data_evolution.py`, `test_max_project_kit.py`, новый request/TS contract test при необходимости.

Interfaces: produces `MAX_MANAGED_SDK_GUIDANCE`; `build_max_agent_guide` сохраняет сигнатуру и возвращает guide с одним SDK block.

- [ ] RED: существующая provider × build/edit/native matrix требует profile.app_name, item.title, hook shapes, business contract и один экземпляр блока.
- [ ] GREEN: выделить блок, устранить релевантные дубли, добавлять после provider replacement.
- [ ] Проверить фактическую request boundary injected completer и настоящий typed SDK fixture без сети.
- [ ] Прогнать связанные kit/catalog/context tests, self-review diff; передать root evidence RED/GREEN и ограничения.

## Task 2: Authoritative bounded compiler repair feedback

Owner: compiler_feedback_worker.

Files: новый общий feedback helper в `apps/api/src/yleum_api/services/`; `generation/agent_runtime.py`, `agent_builder.py`, `agent_native.py`; соответствующие tests. Не менять файлы Task1.

Interfaces: `agent_build_feedback.py::build_compiler_repair_context(detail: str, failure: SourceFailure, files: Mapping[str, str]) -> str | None`; observation field `compiler_repair_context`. `render_model_observation(obs: Mapping[str, Any], *, max_body_chars: int, prefer_content: bool = False) -> str` сохраняет feedback отдельно от clipping общего тела. Root владеет совместимостью; worker может предложить более простой интерфейс с доказательством.

- [ ] RED: confirmed TS2339 получает текущий affected source и реальные объявления profile/catalog types; второй check читает изменившийся snapshot.
- [ ] RED: legacy/native explicit/native early delivery сохраняют context после caps.
- [ ] RED: infra/stale/unowned/unknown/secret/oversized input не расширяет доступ, budgets и proof не сбрасываются.
- [ ] GREEN: внедрить через `checked_fast_check` и существующую `coordinator.source_failure(result)`; общий renderer вместо короткого UI preview для модели.
- [ ] Прогнать связанные repair/progress/budget/deadline tests; self-review diff; передать root RED/GREEN evidence.

## Task 3: Typo-tolerant help and elegant quick-start cards

Owner: help_ui_worker.

Files: `apps/web/src/lib/support-faq.ts`, непосредственно связанные support helper/tests; `apps/web/src/app/max/start/page.tsx` и принадлежащие этой странице styles/tests. SupportAssistant/support.css — только при доказанной необходимости. Никаких lockfile, новых dependencies или общего редизайна.

- [ ] RED: «Создать прилржение», обычные русские формы и синонимы ведут к правильной справке; посторонние/неоднозначные вопросы не получают уверенного ложного ответа.
- [ ] GREEN: компактная нормализация и ограниченное fuzzy token matching; сохранить operator handoff и текущий текст пользователя.
- [ ] Сделать «Подробная инструкция» видимым основным действием карточки; убрать отдельную тяжёлую рамку «Результат этапа», сохранив полезную информацию кратко.
- [ ] Сохранить стиль Yleum: текущая типографика, спокойный синий, точные отступы/границы, явный focus, touch targets, reduced motion и отсутствие overflow на 390 px.
- [ ] Прогнать связанные web tests/types/lint; root проверяет браузером desktop/tablet/mobile, keyboard и ссылки.

## Task 4: Integration, independent review and delivery

Owner: root. Reviewer: independent read-only Sol with review-agent.

- [x] Проверить source/branch/status, сохранить старую dirty работу, создать managed worktree и upstream branch.
- [x] Baseline: 101 focused test прошёл с отдельными test-only env; первая попытка была остановлена отсутствующим JWT_SECRET, env исправлен без product changes.
- [ ] Проверить spec coverage и весь совместный diff; установить только недостающие frozen dependencies при необходимости.
- [ ] Выполнить focused regressions, repo lint/mypy/full applicable tests; полный Linux/DB gate через установленный CI; не считать Windows-ограничения успехом.
- [ ] Получить defect-first независимый review всего diff, исправить P0/P1/P2 и material P3, повторить только затронутые проверки.
- [ ] Intended-files commit → configured upstream push → exact-revision CI → documented production deploy → health/revision proof. Устранить SSH blocker без ослабления security.
- [ ] Выполнить live acceptance из spec на отдельных QA-проектах; без повторных paid calls для уже доказанных случаев и без чужих данных.
- [ ] Обновить отчёт результатами, длительностью/попытками и точными непроверенными границами; не обещать нерушимую стабильность по малой выборке.

## Accepted additions during implementation

- Guide presentation, owner help_ui_worker: компактные реальные скриншоты, крупная схема пути, читаемые шаги/акценты; сохранить15chapter anchors, source/redaction/fullsize evidence. Files: guide/page, GuideVisuals.tsx/.css, guide-page.css, max-guide-surface tests.
- Product learning, owner root: reusable accessible spotlight tour with restrained animation/reduced-motion, per-account/browser first launch in cabinet, manual replay on main MAX/account screens and guide. Existing UI remains functional; no automatic submissions or financial actions. Route-aware steps skip absent/hidden targets, remeasure on scroll/resize, close on route changes, restore focus; first-run dismissal persists across reload. Tour content is local static product guidance, no AI/network.
- Tour/guide shared interface: `components/learning/ProductLearning.tsx::GuideLearning()` renders manual button+guide host; guide headings/anchors stable, diagram `data-learning="guide-journey"`.
- OAuth ЮKassa: user registered app and authorized connection/readiness check, then logged into test Chrome; now prefers built-in browser. Root sent native open request; available automation controls Chrome only. Read-only official-doc/code audit delegated to reliability_primary_research. No live payment/credential exposure; identify callback/env requirements, then configure only through documented secret/deploy path and verify owner-isolated test connection.
