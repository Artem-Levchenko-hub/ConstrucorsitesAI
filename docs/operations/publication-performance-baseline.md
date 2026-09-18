# Publication performance — baseline (P00)

Plan: `MAX_Studio_Publication_Optimization_Plan` (18.09.2026), delivery A.
Machine-readable twin: `publication-performance-baseline.json` (checked by
`apps/orchestrator/tests/test_publication_perf_fixture_contract.py`).

Everything below was measured on production on 2026-09-18 with read-only
observation plus one warm re-publication of the QA-only project
«Клиенты — откаты QA» (`b958d03c…`, source workspace `c0303feb…`, production
workspace `b84ba899…`). No business project was touched, nothing was pruned.

## Base

| Item | Value |
|---|---|
| Git base of the analysis | `78a3f828` (== origin/main at measurement time) |
| api / worker / generation-worker on prod | source `6c4f6233`, image `sha256:d4533162…` |
| orchestrator on prod | systemd from `/opt/omnia`, checkout `6c4f6233` |
| Publication journal | `/opt/omnia-runtime/state/cell-publications/<project_id>/publication.json` (JSON, orchestrator-owned; Postgres has **no** publication table) |
| Only timings that exist today | `history[].response.logs = ["prepare_ms=…","activate_ms=…","total_ms=…"]`, written at the `swapping`/`done` transitions; a failed run keeps none of them |
| Service command of the published app | `pnpm start` — no install/build/migration at startup |

## Measured runs

| Run | Mode | Total | prepare | activate | Source pause | Public unavailable |
|---|---|---|---|---|---|---|
| 2026-09-17 19:09:57Z (journal history) | first (seed + rotation + double switch) | **155.8 s** | 76.7 s | 79.1 s | not observed | not observed |
| 2026-09-18 14:55:48Z run `309ee2a6…` | warm (data seeded, new draft head, rootfs image reused) | **114.1 s** | 66.5 s | 47.6 s | **30 s** (dev + project-postgres stopped at +6 s, restarted at +36 s) | **≈42 s** (no answer +71…+90 s, HTTP 404 +91…+112 s, 200 again at +113 s) |

### Warm run timeline (seconds after the POST, from `docker events`)

| t | What happened | Plan item |
|---|---|---|
| 0–1 | helper `…-c-workspace-source-read-…`: full tar read of the source workspace to hash its revision | C02/C04 |
| 6 | source preview stopped: `-dev` and `-project-postgres` killed | C05 |
| 8–16 | archive helper: export `workspace` (701 MB, 18 476 files) | C02/C06 |
| 17 | export `home` (10 MB) | |
| 17–26 | export `pnpm-store` (617 MB, 15 577 files) | C02 |
| 26 | export `corepack` (empty) | |
| 26–36 | `validate`: second full read + SHA-256 of all archives | C04 |
| 36–37 | source preview restarted (`-dev`, `-project-postgres`), readiness probes | C05 |
| 37–44 | production preflight: `pg_dumpall --schema-only` + compatibility | C07/C14 |
| 44–66 | 4 import helpers into fresh release-scoped volumes (workspace 11 s, pnpm 10 s) | C02/C06 |
| 66.5 | `prepare_ms` recorded, phase → `swapping` | |
| 70 | production gateway removed → **public ingress dark** | C12 |
| 71–72 | old release app **and its PostgreSQL** killed and destroyed | **C09** |
| 72–75 | `project-postgres-prepare` (chown helper), `project-postgres-init`, new PostgreSQL container | **C10** |
| 76–77 | new release app container created and started | |
| 77–90 | app readiness, MAX core `/api/health`, config readback | C12 |
| 90–91 | new gateway created/started; nginx points at it — answers 404 until the boundary checks pass | C12/C15 |
| 113 | HTTPS 200 from the public host; `done` at 114.1 s | |

Docker work in that single warm run: 14 containers created and destroyed (8
archive helpers, 1 source-read helper, postgres prepare/init/server, app,
gateway), 5 new volumes (release-scoped `source/home/pnpm/corepack/next`;
the previous release's volumes stay behind), 211 `exec` calls (readiness,
quiesce, psql), 0 image export/import (rootfs reuse hit). Host: iowait peaked
at 17 %, disk 218 MB/s read / 244 MB/s write, 4 blocked processes.

### Objects moved by a warm publication

| Object | Size | Files | Warm publish |
|---|---|---|---|
| source workspace (`node_modules` + `.next` 82 MB) | 701 MB | 18 476 | exported, hashed twice, imported |
| pnpm store | 617 MB | 15 577 | exported, hashed twice, imported |
| `.next` cache (release-local) | 78 MB | 10 | recreated empty |
| home | 10 MB | 8 | exported, imported |
| corepack cache | ~0 | 0 | exported, imported |
| project PostgreSQL | 39 MB | 987 | not copied (first publish only) — but **restarted** |

≈1.4 GB / 34 000 files cross export → hash → hash → import for a change that
touched a few source files.

## Repeat run with the P01 trace (release `86ad9b78`)

The same release re-published on purpose (`repeat1`, run `202d9368…`):
**110.7 s** total (prepare 65.4 s, activate
45.3 s), source pause 30 s, public unavailable ≈40 s — identical work
to `warm1` although nothing changed (C08: no desired-release no-op yet). The
durable substages now name where the time goes:

| Stage | Elapsed |
|---|---|
| `preflight` | 1.1 s |
| `source_schema` | 4.9 s |
| `capture_rootfs` | 1.2 s |
| `capture_volumes` | 18.1 s |
| `verify_artifacts` | 10.7 s |
| `resume_source` | 3.6 s |
| `prepare_target` | 3.7 s |
| `seed_data` | 22.1 s |
| `activate` | 4.1 s |
| `start_app` | 14.4 s |
| `verify_runtime` | 26.5 s |
| `tls` | 0.0 s |
| `observe` | 0.2 s |

`verify_runtime` (schema digest + MAX core health, config readback, auth
probes, gateway creation) is the single largest activation stage; the gateway
container only appears 18 s into it. `capture_volumes` exported 1.33 GB,
`verify_artifacts` re-hashed 1.43 GB.

## Background reconcile (C13/C15) — confirmed live

The publication reconcile loop rewrites the nginx vhost of every published
project and runs `nginx -t` + `systemctl reload nginx` on **every** sweep:
`nginx.cert_wildcard → shell.run nginx -t → shell.run systemctl reload nginx →
nginx.published_https`, 12 times per hour per published app (288 reloads/day
for one app). The config does not change between sweeps.

## Plan hypotheses vs. evidence

| Item | Status | Evidence |
|---|---|---|
| C01 one long `building` | confirmed (code + UI) | only `prepare_ms` at the `swapping` transition; the panel shows «Собираем приложение» for 66 s |
| C02 warm publish moves dev volumes | **confirmed live** | 1.4 GB / 34 k files, 8 archive helpers |
| C03 rootfs export/import | reuse **hit** on this run | no image events; miss reasons not instrumented yet |
| C04 extra archive passes | **confirmed live** | export hash + `validate` re-read ≈ 10 s |
| C05 source stop/resume | **confirmed live** | 30 s pause of the editor preview |
| C06 sequential helpers | **confirmed live** | helpers strictly one after another |
| C07 late incompatibility check | confirmed (code) | schema compare after capture + resume |
| C08 no desired-release no-op | confirmed (code) | not exercised |
| C09 PostgreSQL recreated on code switch | **confirmed live** | +71 s destroy, +75 s new container |
| C10 recursive chown helper | **confirmed live** | `project-postgres-prepare` helper ran |
| C11 first publish heavier | confirmed (journal) | 155.8 s vs 114.1 s |
| C12 sequential readiness | confirmed (live) | +77 → +113 s |
| C13 full reconcile of all publications | **confirmed live** | nginx reload every 5 min |
| C14 several schema dumps | confirmed (code) | 4 `pg_dumpall` per warm run |
| C15 TLS in the critical path | partially | wildcard short-circuit; reload still runs |
| C16 resource contention | measured | iowait 17 %, disk 93 % full (39 GB free) |
| C17/C18 locks, timeouts | confirmed (code) | 870 s budget, 10 s lock acquire |

## What is not measured yet

- A first publish on a script-owned project (the first-publish numbers come
  from the journal).
- The cache-miss branch of rootfs reuse (C03).
- Bytes actually read/written per stage (only host-level throughput was seen).

## Next

Delivery A continues with P01 (durable substages, heartbeat, byte progress and
`prepare/activate` timings as first-class fields), P02 (panel), P03 (preflight
before capture) and P04 (no-op / config-only). The cheapest confirmed wins are
the nginx reload on unchanged config (C13/C15) and the PostgreSQL restart on a
code-only release (C09/C10, delivery C).
