# Phase 7.1 — Language-agnostic provisioning (grounded design)

> **Status:** DESIGN (Phase 7.1 first sub-slice of the "no-ceiling / multi-stack" phase).
> Implementation (`StackSpec` extraction) + new-stack E2E = follow-on ticks (7.2–7.4).
> **Author:** continuous-quality routine, 2026-06-11.
> **Scope of this doc:** turn the vague goal "Omnia can build in any stack, not just
> Next.js" into a concrete, file-by-file change plan, grounded in the *current*
> orchestrator provisioning code (every coupling below cites real `file:line`, verified
> by grep). Docs-only — no runtime touched by this file.
> North-star alignment: `omnia_north_star` (no ceiling) + `omnia_v2_runtime_live`.

## 0. Where we are (measured, not guessed)

The orchestrator (`apps/orchestrator/`) already provisions per-user dev containers and
**already ships 5 template dirs across 3 languages** — multi-stack scaffolding is *partly
present*, but the provisioning pipeline is still wired to the Next.js+Node assumptions:

| Template dir | Lang | Pkg mgr | Dev cmd | DB |
|---|---|---|---|---|
| `nextjs-entities/` | Node | pnpm | `next dev --turbopack` | Postgres (entity engine) |
| `nextjs-postgres-drizzle/` | Node | pnpm | `next dev` + drizzle-kit | Postgres (drizzle) |
| `vite-react-spa/` | Node | pnpm | `vite dev` | none |
| `fastapi-postgres/` | Python | uv | `uvicorn api.main:app --port 3000` | Postgres |
| `telegram-bot-aiogram/` | Python | uv | aiogram polling | Postgres |

So the *templates* span Node+Python already. The gap is that **the provisioning,
health, hot-reload and prod-build code paths assume the Next.js layout**. A genuinely
new stack (e.g. the Python/FastAPI backend) plugs in only partially today and breaks at
the Next-specific steps.

### What is NOT actually a blocker

- **Internal port.** Every template is normalized to expose **:3000** by convention —
  `fastapi-postgres/Dockerfile.dev:27` runs `uvicorn … --port 3000`, vite uses 3000 too.
  `docker_client.py:126` binds `{"3000/tcp": ("127.0.0.1", spec.port)}`. So today the
  convention "your app must listen on 3000" already lets non-Next stacks run. It works,
  but it is an *implicit contract* baked into one line, not a declared field. We make it
  explicit (StackSpec.container_port) so future stacks aren't forced to 3000.

## 1. The coupling map (verified `file:line`)

Grouped by concern. Every line below was grep-verified on 2026-06-11 (HEAD `e6e8d4d`).

### 1.1 Stack registry — ABSENT (free-form string, no validation)
- `schemas/runtime.py:24` — `template: str` — accepts **any** string, no enum/registry.
- `services/provisioner.py:154` — `image_tag = f"omnia-template-{req.template}:dev"` — the
  only thing that maps a stack name → image is a string-format formula.
- `services/builder.py:80` — `_DEFAULT_TEMPLATE = "nextjs-postgres-drizzle"` — Next is the
  silent default.
- `core/docker_client.py:300-301` — recovers a template by parsing the
  `startswith("omnia-template-")` image-tag prefix.
- **Consequence:** there is no single place that knows "what stacks exist and how each
  behaves". Stack-awareness is smeared across ≥6 files (§1.6).

### 1.2 Internal port — hardcoded :3000
- `core/docker_client.py:43` — field comment `# host port → container's :3000 (Next.js default)`.
- `core/docker_client.py:126` — `ports={"3000/tcp": ("127.0.0.1", spec.port)}`.
- `services/builder.py:236` — prod env `"PORT": "3000"`.

### 1.3 Health / readiness — hardcoded `/`
- `services/runtime_probe.py:59` — `async def probe_runtime_error(name, *, path="/")`.
- `services/runtime_probe.py:94` — `url = f"http://127.0.0.1:{port}{path}"`.
- `services/builder.py:305` — prod healthcheck `url = f"http://127.0.0.1:{port}/"`.
- The probe assumes **"HTTP, on :3000, at `/`"** is the universal readiness signal. The
  existing non-Next templates already pay a *workaround tax* to satisfy it:
  - `fastapi-postgres` ships an idiomatic `/health` (`main.py:62`) but must *also* keep a
    `/` route (`main.py:51`) just so the probe's hardcoded `/` doesn't read as "down".
  - `telegram-bot-aiogram` has **no real HTTP surface** (long-polling) — yet
    `bot/main.py:29-69` bolts a **dummy aiohttp server on :3000** whose only job is
    "satisfy orchestrator's container-up check" (its own comment says so). That dummy
    server is dead weight that exists solely because readiness is hardcoded to HTTP/3000/`/`.

  A declared `Readiness` (HTTP path+codes | Process | Log) lets FastAPI point the probe at
  `/health` and lets the bot drop the fake web server entirely.

### 1.4 Entrypoint / build — Next assumptions
- `services/builder.py:83` — comment `next dev (the live preview)`.
- `services/provisioner.py:194-196` — comment assumes `next dev --turbopack`.
- `services/compile_status.py:1-46` — **Next.js/Turbopack dev-log parser**; the whole
  "compile-error → red card in chat" feature (Phase 6) reads Turbopack glyphs/markers.
  A uvicorn/vite/aiogram log is parsed by Next rules → false negatives.
- `services/builder.py:41-57` — `_OVERLAY_PATHS` hardcodes the Next layout
  (`src`, `entities`, `next.config.ts`, `drizzle.config.ts`, `components.json`,
  `postcss.config.mjs`, `tailwind.config.ts`). Prod build copies *these* paths out of the
  dev container → a Python app has none of them.
- `services/builder.py:88-105` — `_PROD_NEXT_CONFIG` injects Next-specific config.

### 1.5 Hot-reload — drizzle-kit gated on Next paths
- `routers/runtime.py:258-261` — `schema_touched = any(p == "src/lib/db/schema.ts" or
  p.startswith("src/lib/db/migrations/") …)`.
- `routers/runtime.py:266-272` — if touched, runs `npx --yes drizzle-kit push --force`.
- This *no-ops* cleanly for non-Next stacks (the path test fails), which is safe — but it
  means a Python stack that edits its SQLAlchemy models has **no migration step**. The
  "apply schema change after a write" capability is Next-only; it needs to become a
  per-stack `migrate_cmd` hook.

### 1.6 Env contract — Node/Auth.js specific
- `services/provisioner.py:185-192` — injects `NODE_ENV=development`, `DATABASE_URL`,
  `AUTH_SECRET`, `AUTH_URL`, `AUTH_TRUST_HOST` (Auth.js names).
- `services/builder.py:235-241` — prod: same + `PORT=3000`, `HOSTNAME=0.0.0.0`.
- `services/provisioner.py:51-76` — `_integration_env()` injects `MINIO_*`, `SMTP_*`,
  `LLM_GATEWAY_URL` (these are stack-neutral — keep shared).
- A Python app wants `DATABASE_URL` too, but not `NODE_ENV`/`AUTH_*`; it may want
  `PYTHONUNBUFFERED=1`, `UVICORN_*`, etc.

### 1.7 Central spec — `ContainerSpec` (no stack identity)
- `core/docker_client.py:37-51` — `@dataclass(frozen=True, slots=True) ContainerSpec`
  fields: `name, image, port, project_id, env, cpu_quota, memory_mb, network_name, kind,
  restart_policy_name, tier`. **No `container_port`, no `health_path`, no `runtime`/
  `language`, no `migrate_cmd`, no `log_parser` identity.** Everything stack-specific is
  resolved implicitly elsewhere (the smear).

## 2. The design — a single `StackSpec` registry (deep module, one place to change)

Canon: **APoSD deep module** + **Parnas "hide what changes"** + **Clean-Arch dependency
direction** (R-07). Today "what does stack X need" is knowledge duplicated across 6 files
(violates DRY-as-knowledge, R-04). Collapse it into **one declarative registry** that the
pipeline reads; adding a stack becomes editing **one entry**, not six call-sites.

### 2.1 `StackSpec` (new: `core/stack_registry.py`)

```python
@dataclass(frozen=True, slots=True)
class StackSpec:
    name: str                  # registry key == template dir == image suffix
    template_dir: str          # under apps/orchestrator/templates/
    image_tag: str             # "omnia-template-<name>:dev"  (formula kept, but declared)
    container_port: int = 3000 # internal listen port (default keeps current convention)
    readiness: Readiness       # how we decide "up": HTTP path+codes, OR "process"/"log"
    env_profile: EnvProfile    # which env keys this stack wants (Node/Auth vs Python)
    migrate: MigrateHook | None = None   # post-write schema-apply cmd (drizzle / alembic)
    log_dialect: str = "nextjs" # which compile_status parser to use ("nextjs"|"generic")
    overlay_paths: tuple[str, ...] = NEXT_OVERLAY  # prod-build copy-out set

STACKS: dict[str, StackSpec] = {
    "nextjs-entities":         StackSpec(... readiness=Http("/", {200,3xx,4xx-but-served}),
                                         migrate=DrizzlePush(), log_dialect="nextjs"),
    "nextjs-postgres-drizzle": StackSpec(... same family ...),
    "vite-react-spa":          StackSpec(... readiness=Http("/"), migrate=None,
                                         env_profile=NODE_NO_AUTH, overlay_paths=VITE_OVERLAY),
    "fastapi-postgres":        StackSpec(... readiness=Http("/health", {200}),
                                         migrate=AlembicUpgrade(), env_profile=PYTHON,
                                         log_dialect="generic", overlay_paths=FASTAPI_OVERLAY),
    "telegram-bot-aiogram":    StackSpec(... readiness=Process(),  # drop dummy :3000 server
                                         env_profile=PYTHON, log_dialect="generic"),
}
```

`Readiness` is a small sum type: `Http(path, ok_codes)` | `Process()` (container alive +
no crash-loop) | `Log(ready_marker)`. This is the one abstraction that lets the Telegram
bot **delete its dummy :3000 aiohttp server** (use `Process()`), points FastAPI's probe at
its idiomatic `/health`, and removes the "must also expose `/`" tax non-Next stacks pay
today (§1.3).

### 2.2 Touch-points after the refactor (read the registry, don't hardcode)

| File:line today | Change |
|---|---|
| `docker_client.py:126` | `ports={f"{spec.container_port}/tcp": …}` ← from StackSpec |
| `runtime_probe.py:59,94` | probe uses `spec.readiness` (Http path / Process / Log), not literal `/` |
| `builder.py:305` | prod healthcheck uses `spec.readiness` |
| `builder.py:41-57,88-105` | `overlay_paths` + prod-config come from StackSpec, not `_OVERLAY_PATHS`/`_PROD_NEXT_CONFIG` |
| `routers/runtime.py:258-272` | replace the Next-path `schema_touched` + drizzle-kit block with `spec.migrate.run_if_changed(payload.files)` |
| `provisioner.py:154,185-192` | image_tag + env come from `STACKS[name]` (registry, with validation) |
| `compile_status.py` | dispatch on `spec.log_dialect`; add a `generic` parser (regex for `Traceback`/`Error:`/non-zero exit) alongside the Next one |
| `schemas/runtime.py:24` | validate `template ∈ STACKS` (fail fast, R-10) instead of silent fs-miss |

`ContainerSpec` (the Docker-SDK-facing dataclass) gains `container_port` and stays the
**narrow** Docker boundary; `StackSpec` is the **wide** product-level knowledge that
*produces* a `ContainerSpec`. Two layers, dependency points inward (Docker doesn't know
about stacks; the registry knows about Docker).

### 2.3 Migration path (keep prod green at every step — R-10, no big-bang)

Strictly additive, behind the existing default so live apps never regress:

1. **Slice A (pure refactor, behavior-identical):** introduce `StackSpec` + register the
   2 Next stacks with their *current* values (`/` health, 3000, drizzle, nextjs dialect).
   Route the 8 touch-points through the registry. Existing apps must behave **byte-for-byte
   the same**. Verify: regenerate one nextjs-entities app → identical health/hot-reload/build.
2. **Slice B:** register `vite-react-spa` (no DB, no migrate) end-to-end. First stack that
   exercises `migrate=None` + `overlay_paths≠Next`.
3. **Slice C (7.3):** `fastapi-postgres` end-to-end — first **HTTP-at-`/health`** +
   **Python env profile** + **alembic migrate hook** + **generic log dialect**. This is the
   "minimum +1 new stack" deliverable.
4. **Slice D:** `telegram-bot-aiogram` — first **non-HTTP readiness** (`Process()`), proves
   the readiness sum-type isn't just cosmetic.
5. **7.2:** discovery picks the stack from the niche (extend `_infer_stack_from_text` /
   discovery brief to emit the new registry keys, not just static/entity/fullstack).

### 2.4 Risks / open questions

- **Compile-error cards (Phase 6) are Next-bound.** The `generic` log dialect won't be as
  rich as the Turbopack parser. Acceptable: a generic "process exited / Traceback found"
  card beats *no* error feedback for Python stacks. Don't regress the Next parser.
- **Prod build** (`builder.py`) is the heaviest coupling (assumes `next build` output +
  Next overlay). FastAPI prod = a different Dockerfile.prod entirely. Slice C must ship a
  `fastapi-postgres/Dockerfile.prod` + per-stack prod-build recipe; this is the riskiest
  part and should be its own tick.
- **Hibernation / wake / nginx vhost** (Phase 0.3) are HTTP-centric. A Telegram bot has no
  vhost — its "preview" is the bot itself. Out of scope for 7.1; flag for 7.3/7.4.
- **The entity engine is Next-only.** Python stacks won't have the `CrudResource`/registry
  niceties; they're "explicit backend" (the north-star escape hatch), generated freeform
  by the writer against the FastAPI template's SYSTEM_PROMPT.md.

## 3. Definition of done for Phase 7.1 (this slice)

This doc. The grounded coupling map (§1) + the `StackSpec` design (§2) + the additive
migration path (§2.3) are the 7.1 deliverable ("спроектировать generic-провижн"). No
runtime touched. Implementation starts at 7.2/7.3 (Slice A pure-refactor first, so prod
stays green), each its own continuous-quality tick with a live browser/runtime E2E.
