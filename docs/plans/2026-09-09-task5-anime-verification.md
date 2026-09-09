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
No findings after limiting recovery to the top-level shell. New CI and deployment
are pending; local checks alone do not close delivery.

## Delivery boundary

Deploy only API/worker/generation-worker through the documented full Compose
project, from the pushed archive and verified immutable image. Preserve web,
orchestrator, unrelated dirty documents, settings and business data. Require
zero active generations/operations/leases under the write gate; verify exact
images/releases, all six public health checks and anime HTTP bytes/headers.
No user generations or production test data are used.

Before this package API/workers are `85593d30`, web `2ae98520`, orchestrator
`b99af471`. API/compose/release source from the running backend to BASE is
identical. Full-plan customer/model coverage and Tasks3/6/7/12 remain open.
