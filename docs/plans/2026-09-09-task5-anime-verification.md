# Task5 extension: one bundled anime asset

BASE `1c4e86c6820468600980a8edd528c14c95694032`; implementation, review and
delivery use GPT-6 Astra. This package follows the owner's priority: preserve
functionality in smaller, readable code. It does not repeat the delivered kit
CSS/JS extraction.

## Change and preserved contract

Four identical `templates/*/assets/anime.min.js` become one file in the existing
`templates/shared-assets`. The existing `template_materialization.py` adds anime
to `SHARED_ASSETS`, removes its special read branch and creates destination
`assets` before copying. Git does not preserve empty template asset directories.
No additional abstraction, dependency, migration or runtime fetch is introduced.

Canonical production source: **−52,181 bytes, −25 lines, three fewer files**.
The materializer itself is −29 bytes/−1 line; the rest is duplicate vendor code.
This is a modest source reduction, not evidence of faster agent/model execution.
Tests add one custom anime entry to existing export/rollback coverage.

Consumers remain `projects`/`stack_routing` → `repo.init_repo` for new projects;
`public` → `read_kit_asset` for whitelisted HTTP assets; committed project files
for read/export/rollback. Generated assets stay regular standalone files.
User edits, history, paths, MIME/cache/CORS and anime-before-kit order remain.
Frozen `static_templates_810f0fbb.json` is unchanged and fixes bytes and Git modes.
The same module owns filesystem effects; whitelist and populated-destination
failures stay explicit. No new navigation layer, configuration switch or hidden
cache is needed to understand the operation.

## Verification

- Same focused suites BEFORE/AFTER: **163 passed**, 10.02s/5.79s; timing is not a
  performance benchmark. Existing 19 tar deprecation warnings remain.
- Ruff clean; full API mypy clean for 276 source files. Lockfiles/manifests and
  frozen golden unchanged. New asset SHA256:
  `b5ce1be3c3f530f192e0f2571d1942846096d66119cbada34bfdc912c4873f35`.
- Wheel built and installed outside the checkout: public router import and
  three hashes, all five templates materialize/Git bytes and modes/export pass.
  Local focused test DB/Redis URLs use unused loopback port1; no DB fixtures.
- BEFORE exact production API image passes the same five-template smoke in an
  ephemeral network-none, read-only container with only verifier/golden mounts.
  Live anime HTTP bytes match the same hash. Record:
  `/opt/omnia-runtime/releases/task5-anime-baseline-1c4e86c6`.

Raw local commands/logs: `.artifacts/task5-anime/report.md`. Existing CI runs
the focused contracts, installed-wheel smoke, complete API suite on disposable
services and production image smoke. Independent Astra source/helper review:
No findings after limiting recovery to the top-level shell.

Source `50565efd0c6de2b8c6ee66546d6b4fb16cd05673` committed/pushed to main.
CI `34356863327`: all seven jobs passed. Complete API: **3264 passed, 12 skipped,
8 xfailed**, 851.46s. Linux template/wheel/image gates also passed. The exact
pushed API archive built on the production host and passed the same five-template
smoke in isolation. Verified image:
`sha256:47ac83c1f70ee7351812128fcfc56869e8bdd020fe3143ff45207c4fb2cf4664`.

Reproduce from `apps/api` with the documented unused-loopback fixture environment:
`uv run --frozen --python 3.12 pytest -o addopts='' -q tests/test_template_materialization.py tests/test_select_mode.py tests/test_project_export.py tests/test_repo_import.py tests/test_template_registration.py tests/test_template_mapping.py tests/test_prompt_builder.py`;
`uv run --frozen ruff check src/omnia_api/services/template_materialization.py tests/test_template_materialization.py`;
`uv run --frozen mypy src`. Exact outside-checkout wheel and isolated image
commands are in the existing `.github/workflows/ci.yml` template gates.

## Delivered

Only API/worker/generation-worker were recreated through production Compose
project `full`, after zero unfinished generations/operations/active leases under
the verified write gate. All three running containers have the exact image and
release above, zero restarts; six public API health checks pass and both worker
heartbeat releases match. Startup readiness required bounded retries; switch
finished exit0. Nginx bytes restored and POST health405 confirms writes reopened.

Live anime hash, JavaScript MIME, public max-age3600 and CORS remain exact.
Web `2ae98520`, orchestrator `b99af471`, other container IDs/StartedAt and host
orchestrator PID were preserved. Five dirty secondbrain documents are byte-equal
before/after. No user generations or production test data were used. The shared
compose release variable changes desired web metadata, but web was not recreated.

Record: `/opt/omnia-runtime/releases/task5-anime-50565efd/result.json`; build,
smoke, CI, switch, health and comparison evidence are beside it. BEFORE API/workers
were `85593d30`; API/compose/release source from that revision to BASE was identical.
For public reporting, apply only the scoped H148 update and verify HTTP readback;
preserve the existing history. Record that separate step in `publication.json`
beside the runtime result. Full-plan
customer/model coverage and Tasks3/6/7/12 remain open; further Task11 work must
justify real simplification rather than another extraction.
