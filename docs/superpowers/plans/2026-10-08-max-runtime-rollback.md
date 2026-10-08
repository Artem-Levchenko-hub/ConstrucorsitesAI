# Restore the running MAX version after candidate failure

**Goal:** A rejected candidate must restore both accepted source and its running product without modifying business data or promoting the rejected generation.

**Observed failure:** The coffee source was restored to the accepted page while the existing process still served the rejected candidate's compiled page. The rollback introduced in `c6b5b398` checked source only. This blocks the user's four pairs of functional generation acceptance tests.

**Implementation:** Add a fenced `restore_runtime` command which replaces only the product container, verifies the already-applied migration ledger, runs the manifest's declared build/test commands, collects a separate restored compilation receipt and activates the restored runtime. Check the actual source revision before capture and after activation. Failed restoration closes the product; replay of an old terminal operation must not stop a successor. The API verifies the receipt's project/run/epoch/operation/source/manifest bindings before acknowledging rollback.

**Constraints:** Keep project PostgreSQL and workspace volumes, do not apply SQL during restoration, retain normal promotion's stricter product-page compilation checks, and keep generation failure as failure.

- [x] Reproduce the mismatched accepted source and candidate runtime with the existing coffee versions.
- [x] Observe regression failures before implementation for runtime restoration, terminal replay and core-only compilation.
- [x] Implement coordinated restoration and independent receipt validation.
- [x] Independently review cleanup, successor replay, core-only recovery and source changes during build.
- [x] Run final target/full suites, lint/types and real isolated Docker A/B/data restoration test.
- [ ] Commit/push reviewed change, pass CI and deploy exact tested revision through the canonical production release script.
- [ ] Verify release health and repeat the real coffee action/rollback flow.
- [ ] Complete four consecutive pairs with coffee publication in MAX and retained business data; second app has no bot.

The host forwarding prerequisite was delivered in `eb0910d5` through PR 58 and the canonical production release on 8 October. Four accepted generation rounds remain unverified. The existing off-host encrypted backup is `20261008-063957`, SHA-256 `879be5aa7397f03d37863b0a3d1e6bb8f4847cbd0ef71e2b65937cdbb4a57f7d`; backup verification is not a restoration claim.

Final isolated verification: orchestrator 2893 passed / 64 skipped; Ruff and mypy (113 source files) pass. API recovery and bound-receipt checks pass, along with changed-source lint/types. Two opt-in real Docker scenarios pass in 507.75 seconds: accepted product and core-only recovery. Each proves source A / HTTP B before restoration, fresh product container / restored HTTP after it, unchanged PostgreSQL container/volume, retained business row and exact migration ledger. The boundary is mocked in this isolated test; live MAX/browser acceptance remains open. Final independent implementation review found no substantial defects.
