# MAX Product Advisor — design

**Date:** 2026-08-18  
**Scope:** MAX Mini Apps only  
**Status:** approved in chat (`MAX Mini Apps`, one-click apply, up to three suggestions after the first build and material changes)

## Outcome

After a successful MAX Mini App build, Omnia shows at most three contextual ideas for what to add or improve. Each idea explains the user benefit and has an **«Добавить»** button that submits a ready-to-build prompt through the existing MAX chat pipeline. Advice must feel specific to the current app, must not repeat features already present in the code, and must never block generation, preview, or editing.

## Considered approaches

### 1. A second unconstrained LLM prompt after every snapshot

This matches the older roadmap in `docs/05-platform-experience.md` and can produce fluent ideas quickly. It also repeats work after cosmetic edits, can hallucinate features incompatible with the MAX runtime, sends too much project context, and adds cost on every refresh unless a durable cache is introduced.

### 2. A deterministic suggestion catalog only

This is instant, cheap, testable, and available during provider outages. On its own it will eventually feel canned: two apps in the same broad category can receive the same ranking even when their product flows differ.

### 3. Bounded hybrid advisor — selected

A deterministic local analyzer extracts an app archetype and a compact feature inventory from the generated repository. It filters a curated MAX-safe suggestion catalog so already implemented or incompatible ideas cannot reach the model. A cheap model receives only that inventory and candidate list and may rank/rephrase the candidates, but it may not invent arbitrary work. The validated result is cached by material snapshot. A deterministic top-three result is the fail-soft fallback.

This gives the user product-specific reasoning while bounding cost, latency, privacy exposure, and implementation risk.

## User experience

The advisor appears in the MAX chat only when the latest assistant turn has completed with a snapshot. It is hidden while generation is running, while onboarding questions are active, and before the first product snapshot exists.

The compact card is titled **«Что улучшить дальше»** and contains up to three stacked suggestions. Each suggestion has:

- type: **«Добавить»** or **«Улучшить»**;
- short title;
- one-sentence user benefit;
- **«Добавить»** action.

Clicking **«Добавить»** submits the suggestion's prepared prompt through `submitWithCredentialIntake`, exactly like a normal user message. The advisor does not mutate files directly and cannot bypass credential intake, billing/generation policy, snapshots, or the existing agent verification loop. While the prompt is being accepted, only the clicked action is disabled. On success the card disappears with the normal streaming state and returns with refreshed advice after the new snapshot.

Failures are quiet and non-blocking. If model ranking fails, the deterministic fallback is shown. If the advice endpoint itself cannot load a usable snapshot, no card is rendered and normal chat remains unchanged.

## Material-change policy

The client requests advice after every completed MAX snapshot. The server chooses an **analysis snapshot**:

- the first generated product snapshot is always material;
- prompts that add or change a product flow, screen, data behavior, integration, notification, search, analytics, roles, or business action are material;
- prompts limited to color, typography, spacing, wording, icon, or other cosmetic adjustments are not material;
- for a cosmetic snapshot, the most recent material analysis snapshot is reused.

The cache key includes advisor version, project id, and analysis commit SHA. Therefore cosmetic edits make no model call, reloads and multiple tabs reuse the same advice, and a new meaningful product version gets fresh advice.

## Backend architecture

### `services/product_advisor.py`

A focused module owns:

1. material-change classification;
2. bounded source inventory extraction;
3. archetype-specific MAX suggestion catalog and presence signals;
4. candidate filtering and deterministic ranking;
5. construction of the bounded model prompt;
6. strict parsing and validation of model output;
7. deterministic fallback.

The analyzer reads text files from the selected commit through the existing `repo.read_files`. It ignores environment, secret, generated dependency, binary, and lock files. It does not send source code to the model. The model sees only project name, product archetype, normalized feature signals, the latest material request, and the already filtered candidate ids/titles/benefits/prompts.

The existing design-intelligence classifier is exposed through a small public helper so design generation and product advice use the same archetype rather than maintaining divergent product taxonomies.

### `routers/product_advice.py`

An authenticated idempotent endpoint is added:

`POST /api/projects/{project_id}/product-advice`

It verifies ownership and `template == "max_miniapp"`, resolves the current and analysis snapshots, checks Redis, loads the selected commit when needed, invokes the bounded advisor, caches the validated response, and returns it. Non-MAX projects receive 404 so this feature cannot leak into the ordinary site builder.

Redis keys use the form:

`omnia:product-advice:{version}:{project_id}:{commit_sha}`

Model-ranked advice is cached for 30 days. A deterministic fallback caused by a transient model failure is cached for 15 minutes so the service can retry later. Redis failure is fail-open: analysis continues without caching.

### Model use

The advisor uses `PRODUCT_ADVISOR_MODEL` (default `claude-haiku-4-5`) with a 700-token output limit and temperature `0.1`. Its output is a JSON list of candidate ids plus optional bounded title/benefit rewrites. Unknown ids, duplicate ids, unsafe shapes, more than three items, and empty implementation prompts are rejected or replaced from the server-owned candidate definition.

Automatic advice does not debit the user's generation balance. The model call is tagged `stage=product_advisor` for operational visibility and is isolated from the build context.

## Frontend architecture

- `lib/api/product-advice.ts` defines the response contract and idempotent request.
- `components/max/MaxProductAdvisor.tsx` is a presentational compact card.
- `ChatPanel.tsx` enables the query only for `mode="max"`, a completed latest assistant message, a real `snapshot_id`, and no active onboarding survey.
- The React Query key includes project id and snapshot id. Server-side material-snapshot caching prevents cosmetic versions from creating extra model calls.
- Applying a suggestion reuses `submitWithCredentialIntake`; no parallel mutation path is introduced.

## Advice content rules

The first catalog covers the existing shared archetypes: commerce, booking/services, fitness/health, communication/community, learning/content, operations/CRM, analytics, and general productivity. Candidates prioritize improvements that reduce an end user's work or increase repeat value: faster repeat actions, search/filtering, saved state, progress, reminders, status transparency, useful empty/error states, and relevant integrations.

Every implementation prompt must:

- describe one coherent vertical slice;
- preserve current working behavior and visual language;
- require real interactions and persisted state where the feature needs it;
- require loading, empty, error, and success states where applicable;
- stay within the generated MAX app and its supported integration/runtime contracts;
- avoid claims that an unavailable provider is connected.

## Security, privacy, and reliability

- Authorization and MAX-template checks happen before repository access.
- Raw project files, environment values, credentials, chat secrets, and user identifiers are never placed in the ranking prompt.
- Repository scanning is size-bounded and extension-allowlisted.
- Advice cannot execute code or tools. Only an explicit user click enters the normal generation pipeline.
- The model is advisory; strict server validation and a deterministic fallback own the response contract.
- Redis, the model provider, and malformed model output are non-blocking dependencies.

## Testing

Backend unit tests cover material/cosmetic classification, source filtering, archetype selection, present-feature suppression, stable top-three fallback, prompt safety, strict model parsing, and cache-key stability. API tests cover authentication, ownership isolation, MAX-only access, cache reuse across cosmetic snapshots, recomputation after a material snapshot, and model/Redis failure behavior.

Frontend tests cover MAX-only rendering, the completed-snapshot gate, maximum of three cards, button-to-chat handoff, loading/disabled behavior, and absence during streaming/onboarding. Repository checks include API Ruff and targeted Pytest, web Vitest, TypeScript, lint, and production build.

## Out of scope for this slice

- advice for ordinary websites or non-MAX projects;
- autonomous implementation without an explicit click;
- a long-term user preference/memory profile across projects;
- collaborative voting, dismissals persisted to the database, and analytics dashboards for advice acceptance;
- free-form model suggestions outside the server-owned catalog.

Those can follow once click-through rate and successful-build rate establish which advice is genuinely useful.
