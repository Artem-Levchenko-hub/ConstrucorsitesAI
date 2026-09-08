# Task 5 — static template materialization

Status: implemented, reviewed, pushed and deployed with correction release 4960f6b9.

Implementation: `777f3f61ba377da9fb2eae0008f47f4f7faa0704`, pushed to origin/main.
CI run `34249743033`: API critical regressions, MAX starter, Task 5 consumers,
installed wheel, production image and isolated image smoke passed. Full API suite
reported 14 failures. Thirteen non-database failures were reproduced on both
baseline `810f0fbb` and current code with identical errors; baseline 5.09 seconds,
current 2.52 seconds. The migration roundtrip failure was not run locally because
it creates/drops a PostgreSQL database; its test upgrades to head but still
expects 0056 while head is 0060.

The unrelated P01 Docker fixture initially exited 137 while writing its 129 MiB
file in a 64 MiB helper. A rerun of the orchestrator job passed; no P01 code or
assertions were changed. This transient fixture failure remains recorded.

Full-suite classification:

- Real onboarding wire bug: `SurveyQuestion` instances are passed directly to
  `publish_event`; Redis JSON serialization uses `default=str`. Clients receive
  strings instead of question objects. Do not weaken the failing test.
- Four offline-manifest failures share missing cabinet coverage. Do not fabricate
  a successful cabinet verdict or silently drop it from the expected gate universe.
- Nine failures concern stale tests: current backend-default flag, multiline
  entity guard, brand-variable injection/idempotence, primitive contract text,
  explicit migration target, execution owner column, disabled legacy provider,
  and signed bootstrap navigation versus ordinary render navigation.

The owner has been asked to authorize a separate corrective package for the
onboarding bug and test baseline. No answer has yet been received. Under the
approved R/O-only plan, do not silently make behavior changes or bypass the red
full-suite gate. A separate question about Task 3 numeric serialization is pending.

Baseline: `810f0fbbbdd46391b8a01cb0ce9a8891533f8666`. Local branch
`codex/project-cell-cloud-20260902`, upstream `origin/main`; production checkout
has the same baseline. Five unrelated secondbrain documents on production remain
dirty and must be preserved. Existing local untracked artifacts are not part of
this delivery.

## Contract and ownership

- `repo.init_repo` is the only production consumer that materializes API templates.
  Both project creation and first-build routing reach it. It uses a fresh directory.
- `routers.public` reads kit resources at import; its whitelist, MIME types, cache
  and CORS behavior remain unchanged.
- `template_materialization` owns the two shared static kit assets and fresh
  scaffold copying. It recognizes exact bundled paths, not arbitrary directory names.
- Export, read, import, duplication, generation injection and rollback continue
  using committed project files. They do not overlay current shared resources.
- Static output still contains ordinary `assets/omnia-kit.css` and
  `assets/omnia-kit.js` files. No runtime dependency or symlink was introduced.
- The four anime copies and orchestrator/inspector assets are outside this package.

## Independent baseline

`tests/fixtures/static_templates_810f0fbb.json` was captured from baseline Git
blobs before implementation: every path, SHA256 and mode in four static templates
and the fullstack scaffold. Windows CRLF checkout bytes were normalized to the
baseline Linux bytes for local tests, with original bytes backed up under local
`.artifacts/refactor-task5-20260908/windows-original`; this is not a source change.

Initial seven real-pygit2 characterization checks passed before refactoring.
After extraction, 147 template, select-mode, prompt-builder, export, mapping and
registration tests passed on Python 3.12. Full API Ruff and mypy passed for all
274 source files. Independent read-only review found no actionable defects and
independently checked 103 tests plus every golden entry against baseline Git.
An initial focused invocation lacked the fixture JWT_SECRET; its
seven settings failures disappeared after supplying the explicit test environment.

## Test environment and delivery gates

Local test database and Redis URLs point to unused loopback port 1; these focused
tests do not use database fixtures. Never run database tests against production.
An isolated Python 3.12 environment was installed from the unchanged lockfile.
Local Docker Desktop cannot start: Inference manager fails to open its
`dockerInference` socket. Linux packaging validation will use isolated CI.

The actual wheel was built and installed without dependencies into a separate
target. Outside the checkout, Python 3.12 imported the installed public router,
verified kit hashes, and passed all five materialization/Git/export fixtures.
`.gitattributes` pins template LF bytes across Windows and Linux without changing
production bytes. Six duplicate resource copies remove 6132 maintained lines
and 332652 source bytes; runtime latency improvement is not claimed.

Production preflight: zero unfinished generations, cell operations and active
leases; API/worker/generation-worker use image `04233b77dc3482b9a435cfd014513147294e8f8c43398037ae5d74890e2acf45`
and release `179a3b3f321b58400351f39880bea902af681fca`; orchestrator health reports
`1011a0fdf7cc4f636e7550937c0e89447fc21bcd`. API sources between its existing
release and Task 5 baseline are identical. Repeat activity checks at rollout.

Image smoke also passed on the production host under an unused image tag;
image `sha256:65700e1d3c7b20e995ad2b01f08c5eced8298df257364fba704905dab3823701`.
No production containers were restarted. The build staging record is
`/opt/omnia-runtime/releases/template-image-777f3f61`; the API-only delivery script
is prepared locally but has not run. Its independently reviewed recovery path
keeps the write gate closed if rollback cannot verify the old API/worker images.
Remaining delivery blockers are the baseline corrections/approval and a green
full gate, followed by the documented active-operation/deployment/health checks.

After this package, continue the approved R/O plan one delivered package at a time.
P01 is already delivered; Tasks 3–4 and 6–13 remain. Do not add behavior changes,
P12, production data experiments or user generation runs.


## Delivery completed after baseline corrections

CI 34258366570 on 4960f6b99909e44efc421b6e2c99140d55cbd43b passed. Full API:
3129 passed, 12 skipped, 8 xfailed in 699.09s; 0056/head roundtrip passed.
Orchestrator: 1244 passed, 30 skipped, 15 xfailed; P01 live Docker: 9 passed.
The first orchestrator job produced no test output for 18 minutes and was
cancelled only after API completed. The same-SHA job rerun passed in 112.32s;
this unexplained CI hang remains recorded, not reclassified as a code fix.

Independent combined review: no findings. Focused API 423 passed; full API Ruff
and mypy clean. Web wire consumer: 2 interaction tests, typecheck and ESLint clean.
The earlier blocking paragraphs above describe the pre-correction state.

The documented full Compose project now runs API and both workers at 4960f6b9,
image sha256:8a44eb141a52c21e9d9615a7991c8b21316a6b7c2e1609c717d04ab58658b670.
The exact image passed isolated five-template materialize/Git/export checks and
both onboarding wire tests before rollout. API health/release and public kit
hashes passed after rollout; write gate removed. Orchestrator stayed active;
web and orchestrator were not restarted. Unrelated dirty diff SHA256 stayed
0faee2b7c90dfd954d0def658d80cd89f21312061c500b2d9efd6ae06f1d250d.
Private backup/result record: /opt/omnia-runtime/releases/startup-latency-4960f6b9.
No user generations or production test-data writes were performed.
