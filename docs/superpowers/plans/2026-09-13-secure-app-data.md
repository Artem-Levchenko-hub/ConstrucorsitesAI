# Secure application data implementation plan

> **For agentic workers:** Use task-by-task implementation with independent read-only security review. Primary owns Git, migrations and deployment.

**Goal:** Provide trusted encrypted CRUD and protect new generated application databases by default.

**Architecture:** Extend the existing MAX core/session boundary, and the existing PostgreSQL actor/RLS controller. Keep encryption keys out of generated code and retain ciphertext identity across process restarts.

**Tech Stack:** Node 22 crypto, TypeScript, PostgreSQL/pg, Python controller, existing pytest/Node test runners.

**Spec:** `docs/superpowers/specs/2026-09-13-secure-app-data-design.md`

## Global constraints

- Preserve existing business data and unrelated working trees.
- No plaintext fallback, no keys derived from auth secrets, no application administrator for fresh databases.
- Manual user acceptance is performed by the user; run automated checks and deployment health.
- Disk encryption and separately managed keys require verified infrastructure, not a configuration claim.

## Execution

- [x] Add behavior tests for authenticated encryption, context swapping, key versions and missing-key refusal, then implement the trusted crypto module.
- [x] Add full encrypted record CRUD, bounded API routes and typed browser client; test ownership, pagination, optimistic revision conflicts and database persistence.
- [x] Stage `DataContract(version=1, tables=[])` before a fresh project receives database access; install policy before product startup, retain existing protection/recovery behavior.
- [x] Include managed code/migration files in kit and compiled core, update generation instructions and compatibility/version markers.
- [x] Implement Vault Transit key provider, immutable tmpfs mounts, rotation retaining historical DEKs, fail-closed runtime integration.
- [ ] Activate an externally operated key service and prove its backup/restore; service location is awaiting user input. No plaintext-key fallback is permitted.
- [ ] Independently review full diff, fix actionable defects, run applicable CI tests/lint/types/build.
- [ ] Write manual test steps and exact operational prerequisites, commit intended changes, push configured upstream, deploy exact SHA and verify service health when prerequisites are satisfied.

Verification anchors: `node --test` for real crypto and CRUD modules, existing `uv run pytest` protection/lifecycle tests, disposable PostgreSQL for persistence/RLS, template TypeScript build, `git diff --check`. Extend existing tests rather than replacing observed failures with source-string assertions.

## Verified checkpoint

- Independent Astra review: three concrete defects fixed (key-version wire type, pagination index, gateway Origin forwarding); final review returned no findings.
- Disposable PostgreSQL + real HTTP handler: 12 tests passed, including actual migration twice, reopened pool/keyring, cross-user/project denial, concurrent writes and audit rollback.
- Linux key-provider/runtime tests: 32 passed. Immutable core Linux build passed. Local TypeScript and both Python mypy gates passed.
- Full orchestrator local run: 1565 passed, 83 skipped, 15 xfailed; six Windows encoding failures were followed by a passing 60-test nginx suite with Python UTF-8 enabled.
- Windows standalone packaging fails with EPERM symlink; Linux build is the deployment artifact.
- Pending: final API test retry after SSH transport reset, upstream delivery and health. KMS activation, whole-volume encryption, legacy-data conversion, role-based sharing and automatic full Project Cell off-host coverage remain unimplemented/unverified; see operations document. Do not label the entire original concept complete.
