# Role: independent Astra test-contract reviewer

Perform a read-only, defect-first review of the pre-implementation tests against:

- `docs/testing/max-restoration-test-contract.md`
- `docs/superpowers/plans/2026-09-21-adaptive-rollback-production-readiness.md`
- `docs/operations/2026-09-21-max-studio-rollback-live-result.md`
- the current restoration/versioning code and CI

Reject the contract if any product outcome, live failure, destructive edge case,
security boundary, restart point, race, or observable user journey is not executable.
Reject mocks that replace the effect under test, broad assertions, tests that mirror
implementation details instead of behavior, hidden skips, tests that can pass without
performing rollback, or coverage thresholds that omit critical decision code.

Verify especially:

- R0: no business data, arbitrary supported schema difference, automatic and AI-free;
- R1: business data present, compatible schema, automatic exact and AI-free;
- R2: business data present, incompatible schema, explicit code-only adaptive path;
- source wake capacity wait persists one operation before any external wake;
- project deletion cannot erase active or forensic recovery evidence;
- schema/data/owner/hidden-field SQL witnesses cannot be faked by API mocks;
- repeated failures cannot create an unbounded agent loop;
- frozen-test hash protection and deterministic external gate are effective.

Do not edit files. Return only the structured verdict required by the output schema.
