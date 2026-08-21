# CI

CI is active in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). It
gates pushes to `main` and pull requests on the locked pnpm install, web
typecheck/tests/build, gateway tests, Python syntax, and `actionlint` validation
of every workflow.

The paid public-API build/edit monitor lives in
[`production-generation-canary.yml`](../../.github/workflows/production-generation-canary.yml).
It is always available through `workflow_dispatch`; its daily schedule runs only
when `PRODUCTION_GENERATION_CANARY_ENABLED=true`. The workflow requires the
`PRODUCTION_EXPECTED_RELEASE_SHA` repository variable and the
`PRODUCTION_CANARY_EMAIL` / `PRODUCTION_CANARY_PASSWORD` secrets.
