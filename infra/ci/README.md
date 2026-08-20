# CI

`github-actions-ci.yml` is the minimal push-gate workflow (fresh-plan Step 0): it
runs only the baseline-green checks — `apps/web` `tsc --noEmit`, `apps/llm-gateway`
`pytest`, and a dependency-free `compileall` of `apps/api` + `apps/orchestrator`.
The heavy api/orchestrator pytest suites need Postgres/Chromium/docker +
`/opt/omnia-runtime` and are intentionally excluded, so a red run means a real
regression, not env noise.

**To activate:** move it to `.github/workflows/ci.yml` and push with a token that
has the `workflow` scope (the deploy PAT used on 2026-07-05 lacked it, so GitHub
refused a workflow-file push — that's why this lives here as a reference).
