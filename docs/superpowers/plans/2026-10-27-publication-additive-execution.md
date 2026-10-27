# Publication additive execution implementation plan

> For agentic workers: execute bounded owned tasks; primary alone owns Git, dependency/CI changes and deployment. Preserve unrelated work and all data/identity/schema guards.

**Goal:** Close the real published-order preservation scenario for a generated nullable-column feature.

**Architecture:** Kubernetes target observer, verified encrypted logical backup, existing additive resolver/authorization, durable transaction-XID execution/recovery, owner API confirmation, then normal publication. Scope and invariants are in the spec.

**Tech Stack:** Existing FastAPI/Pydantic/SQLAlchemy, Kubernetes exec client, Docker SDK, PostgreSQL, pglast, cryptography, Next.js/TanStack Query and existing tests.

**Spec:** `docs/superpowers/specs/2026-10-27-publication-additive-execution-design.md`.

## Global constraints

- Nullable additions only, Kubernetes production only; unsupported changes fail closed.
- No client SQL, fake backup flags, manual DDL, receipt-table schema changes, unknown-outcome retries, live backup restore, source-lease bypass or new model calls.
- Exact owner/source/snapshot/fence/storage/backup binding and publication locks throughout effects.
- Bounds and private artifact handling; outputs contain metadata/digests only.

## Tasks

- [x] Reproduce real UI publication rejection and prove old public records unchanged.
- [x] Identify actual target Kubernetes and current accepted source; isolate worktree at0a292c55.
- [x] Existing plan/resolver fast baseline103PASS; record Windows long-ID limitation for complete Linux CI.
- [x] Independently challenge transaction/backup/recovery design before mutation implementation.
- [x] IO worker: production catalog/session transport, consistent snapshot backup, exact-owned isolated restore verifier; failing tests first and focused tests. Final transport/IP gate review and real CI remain.
- [x] Controller worker: durable bound prepare/confirm/status/recovery, canonical transaction execution, publication/drain guards; focused tests pass, real PG cases await CI.
- [x] Primary: owner-authorized API/admission integration, frontend prepare/poll/confirmation, source-stale behavior and tests.
- [ ] Wire missing declared development dependencies, real disposable PostgreSQL/restore CI and required lint/type/build gates.
- [ ] Full independent diff review; fix findings, commit intended files, push, exact-head CI/merge.
- [ ] Canonical production deployment after active work/drain checks; exact SHA and HTTP health proof.
- [ ] Live prepare/confirmed migration/republication, four old MAX orders unchanged, new scheduled and immediate orders persisted after re-entry.
- [ ] Resolve or explicitly report external ambiguous model request and remaining app history display issue; no blind paid retry.

## Current checkpoint

Platform implementation and focused regression coverage are present in this worktree, not yet committed or deployed. Independent review found and fixed lost-outcome exclusion, trusted managed-ledger validation and psql boolean serialization defects; final transport boundedness review and real PostgreSQL/Docker CI remain. Local API mypy targeting Linux passed all363 source files; frontend build/types passed,1374 frontend tests passed in the full run and the remaining new flow passed after disabling demo mocks in its fetch fixture. Local modules were reused and differ from the current frozen Next patch version: frozen CI is still mandatory.

Old publication remains live. The fourth generation completed in7m18s and accepted app b1315fec4fdb5b8edeb74043bd20702cf69e843a, snapshot ace5f776-5608-4e32-9126-f6a8f21c499f. Its only increment from6ef is the missing main-history pickup/comment JSX; API/SQL are byte-identical and the14 handler checks remain applicable. Independent review reports no findings in the final platform and app changes; real PostgreSQL/Docker CI and live acceptance are still required.

Third history-only generation failedHTTP422 and its provider financial outcome remains ambiguous and untouched. User explicitly funded llmgw.ru and authorized continuation; earlier resends were rejected by the canonical deployment drain before a new generation was reserved. The unrelated canonical release serves fefb2f19d0ad6d27ed23e6c24ef595cfe20b8269, health confirmed; this worktree fast-forwarded to it without overwriting its45 intended modified/new files. Read-only live metadata confirms a literal-IP Kubernetes endpoint and no explicit proxy; inspect the actual service's ambient-proxy boolean before activation. Evidence/checkpoint is outside the checkout at `Documents/Codex/Yleum-Published-Data-Preservation-2026-10-27/`.
