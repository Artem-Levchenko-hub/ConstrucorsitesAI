# Task 5 — static template materialization

Status: local verification and independent review passed; CI and deployment pending.

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

Remaining: normal Docker image smoke, full API CI and baseline analysis for any
failures, commit/push, active-operation gate, canonical API/worker deployment,
exact runtime identities, live kit response evidence and report completion.

After this package, continue the approved R/O plan one delivered package at a time.
P01 is already delivered; Tasks 3–4 and 6–13 remain. Do not add behavior changes,
P12, production data experiments or user generation runs.
