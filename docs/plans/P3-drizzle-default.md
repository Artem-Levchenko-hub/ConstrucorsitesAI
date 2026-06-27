# P3 — Real drizzle backend as the default stack (canary-first, blast-radius-aware)

> Produced by the `p3-drizzle-default-readiness` workflow (4 parallel readers + synthesis),
> 2026-06-27. Goal: make a real Drizzle/Postgres backend the default generation stack instead
> of the managed entities/`records` model — SAFELY, dark-shipped behind a flag + per-user canary,
> never breaking prod. Reuses the patterns proven on the realtime messenger this session.

## How stack selection works today (entry points)
- `web_app → nextjs_entities` mapping: `apps/api/src/omnia_api/services/discovery.py:67-79` (`_RESULT_TYPE_TO_STACK`, `result_type_to_stack()`).
- Default decision: `apps/api/src/omnia_api/routers/messages.py:1176-1220` (legacy path = `_infer_stack_from_text`; router path gated by `use_result_type_router`).
- Drizzle template = orchestrator template `nextjs-postgres-drizzle`; API template `fullstack` maps to it (`apps/api/src/omnia_api/schemas/project.py:53,74-81`).
- `fullstack` is currently **unreachable** via discovery — a canary override is the only entry point.

## Canary mechanism (mirror `agentic_builder_canary_users`)
1. `core/config.py` (next to `agentic_builder_canary_users` ~line 783): add
   `use_drizzle_default: bool = Field(default=False)` (env `USE_DRIZZLE_DEFAULT`) +
   `drizzle_default_canary_users: str = Field(default="")` (env `DRIZZLE_DEFAULT_CANARY_USERS`).
2. Generalize `is_agentic_enabled` (config.py:738-753) → reusable `is_in_canary(global_flag, csv, user_id)`; call from both sites.
3. Early override in `messages.py` BEFORE the stack block (line 1176), first-build only: if canary-enabled
   AND inferred stack would be the data/CRUD stack (`nextjs_entities`/web_app), force `_inferred_stack = "fullstack"`.
   Leave the realtime override (messages.py:1230-1240) untouched + AFTER, so messenger prompts still win.
   -> dark-ships drizzle to canary users only, instant global/per-user env rollback.

## Template-parity fixes (ZERO blast radius — drizzle template is unreachable today)
Mirror the realtime fixes already shipped this session. Source = `nextjs-realtime` template.
1. `src/lib/utils.ts` (cn) — copy from realtime. Highest-leverage missing primitive.
2. `src/components/ui/*` — port the 18 shadcn components (drizzle has ZERO).
3. `package.json` — add the `@radix-ui/*` set + cva (drizzle has only `react-slot`).
4. `src/app/api/auth/register/route.ts` — JSON register (POST {email,password}, hash, insert, `roleForNewUser()`); pairs with the gate's csrf+callback login.
5. Align signin/signup to csrf+callback NextAuth flow (keep auth-shell visual shell).
6. Rewrite `SYSTEM_PROMPT.md` — drop the "shadcn NOT pre-installed" line; add cn/ui-kit/register/`drizzle-kit push` sections.
7. Hot-reload chain already correct: `docker-entrypoint.sh` runs `drizzle-kit push --force`; `runtime.py:303-306` auto-pushes on schema.ts changes.

**Deploy trap:** template CODE fixes are NOT live on git pull alone -> must REBUILD `omnia-template-nextjs-postgres-drizzle:dev` (only needed once canary routes to drizzle).

## Blast radius (make stack-agnostic before flipping GLOBAL default)
| Item | Risk | Fix |
|---|---|---|
| Demo seeding is 100% entities-specific (`demo_seed_writer.py`, `demo_seeder.py` read `entities/*.json`) -> drizzle ships EMPTY (breaks WOW-from-empty) | HIGH | drizzle-aware seeder via SDK/Server Actions, or accept empty on canary + measure |
| `CrudResource` + entities SDK are entities-only; agent must use Drizzle queries/Server Actions | HIGH | SYSTEM_PROMPT steer; make `art_director_writer` brief stack-aware (schema.ts not entities/*.json) |
| `functional_gate.py` (G004) is realtime-only (`messages.py:2708`) -> drizzle apps ship typecheck-only | HIGH | add `run_drizzle_functional_gate` (signup->login->create-record->read-back-persists->outsider-403, reuse `_login`/`_api`/`summarize`); extend dispatch to `_orch_name in ('nextjs-realtime','nextjs-postgres-drizzle')` |
| entity post-gen guards (`messages.py:1560+`) no-op on drizzle, no drizzle schema-sanity guard | MED | lightweight `drizzle-kit push` dry-run guard or rely on functional+green gate |
| `fullstack` unreachable today; canary override is sole entry | MED | add `test_stack_routing` for fullstack->nextjs-postgres-drizzle provisioning |
| `CONTAINER_NEXT` lists already include `fullstack` (preview/rollback/runtime/style_patch) | LOW | no change; optionally dedupe the 5 copies |
| prod overlay hardwires `entities` path (`builder.py:42-57,99-101`) | LOW | verify a drizzle prod build succeeds in the gate harness |

## Verify (each step)
- Unit: `test_stack_routing` fullstack->drizzle provision; `is_in_canary` truth table; messages.py override only on canary+data intent; `run_drizzle_functional_gate` verdict tests.
- Live E2E (worker-isolation harness): provision drizzle as canary -> csrf+callback login -> create record -> reload -> PERSISTS in per-project Postgres -> outsider 403. Drizzle analog of the realtime 11/11 gate.
- Live: dev container boots, `drizzle-kit push` materializes tables, signup->login from first request, schema.ts edit hot-reloads. Prod Docker build green.
- Before deploy: typecheck/lint + REBUILD `omnia-template-nextjs-postgres-drizzle:dev`.

## STOP/GO for flipping the GLOBAL default
Flip `use_drizzle_default`/discovery only after ALL: (1) >=20-30 real canary first-builds across
CRM/booking/catalog/dashboard pass the drizzle functional+security gate with no regression vs entities;
(2) no increase in build-fail/blank-app/unhealable rate vs entities on the same prompts; (3) demo-seeding
parity (no empty-on-provision) OR explicit owner OK that empty-first is acceptable; (4) prod dev+prod
Docker builds green + schema hot-reload verified. Until all four hold: global OFF, only widen the canary CSV.

## First safe step
Copy `cn()` -> `nextjs-postgres-drizzle/src/lib/utils.ts` from realtime. Pure additive, breaks nothing
(drizzle unreachable today), prerequisite for the ui kit, smallest committable unit. Then the rest of
template-parity (ui kit, radix deps, register endpoint) — all additive, zero blast radius.
