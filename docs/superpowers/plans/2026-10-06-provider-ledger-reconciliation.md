# Provider ledger reconciliation implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Work in the isolated reconciliation worktree; Git/deployment remain serial.

**Goal:** Reconcile authorized LLMGW balance-ledger operations by exact request IDs and integer kopecks without changing original receipts or customer wallets.

**Architecture:** Record the configured provider organization on new call admissions. Import an operator-verified normalized statement into append-only entries, conflict observations and confirmations. Match organization + exact ref_id only; report missing scope, duplicate usage references and conflicts explicitly. No browser credential extraction, guessed XLSX mapping or new public export endpoint.

**Tech Stack:** Python 3.12, asyncpg, PostgreSQL 16, Alembic; existing locked environments.

**Spec:** User's 6 October continuation instructions in this session; the operator contract and remaining production blockers will be retained in docs/qa/2026-10-02-handoff/evidence/provider-ledger-reconciliation-20261006.md.

## Global constraints

- Import never updates provider_calls, Usage, usage settlements or wallets.
- Organization binding is explicit and prospective. Historical NULL organization is not automatically backfilled.
- delta_kopecks is a signed strict integer; confirmed expenses use its negative, without FX or floating point.
- Separate immutable source confirmation does not turn an estimated provider response into a reported-RUB response.
- Raw provider IDs remain in a restricted report, not chat/log output.
- Actual production verification and an authoritative source file are required before declaring reconciliation complete.

## Review focus

- Conflicting copies of one operation in the same batch must prevent confirmation.
- A second usage operation with the same ref_id must invalidate automatic reconciliation of both.
- Provider corrections remain independent signed entries, never edits of the original charge.
- Concurrent imports must not duplicate entries/confirmations or customer charges.
- Missing organization on historical calls stays unresolved even if ID and amount match.

### Task 1: Strict statement contract

Files: API services/provider_ledger.py; tests/test_provider_ledger_contract.py.

Interface: parse_statement(data: bytes, *, expected_organization_id: str, source: bytes) -> Statement.

- [x] Write and run RED tests for integer precision, source hash, organization mismatch, unknown fields, missing ref_id and corrections.
- [x] Implement the bounded normalized JSON parser; verify GREEN.

### Task 2: Durable reconciliation and prospective organization

Files: migration 0080_provider_ledger; API ProviderCall model; gateway config/provider_calls and admission tests; API service; gateway integration_tests/test_provider_ledger_postgres.py; migration head tests; CI joint PostgreSQL command.

Interface: import_statement(connection, statement) -> restricted report; report_organization(connection, organization_id) -> restricted report.

- [x] Write and run RED PostgreSQL tests for import/replay/concurrency, conflicts, foreign/missing scope, duplicate usage IDs, corrections, immutable tables and unchanged billing records.
- [x] Add nullable provider_organization_id for new admissions and append-only ledger tables with database triggers; no historical backfill.
- [x] Implement deterministic matching and totals in integer kopecks; verify all physical tests plus existing paid billing contracts.

### Task 3: Operator tool and delivery

Files: API scripts/reconcile_provider_ledger.py; operator contract/evidence document.

- [x] Test restricted report creation without overwrite, source validation before DB access and safe error output.
- [x] Add CLI with explicit expected organization and source file; no provider HTTP calls or cookies.
- [ ] Run lint/types, migration sanity, relevant full suites and independent review. Commit/push/CI, deploy only by canonical documented route and verify health.
- [ ] If approved server access or authoritative ledger is unavailable, record the exact blocker and leave production reconciliation unaccepted.

## Execution checkpoint

- Base source/main: 09a9d8a4b2cf80b40b0754c0debbb9ec4430524b; isolated branch codex/provider-ledger-reconcile-20261006.
- Watched RED then GREEN: parser, organization admission, physical import/conflicts/replay, output file protections, safe CLI arguments, reverse-order test isolation, correction-only estimate comparison, model registration.
- Local final checks: 485 whole gateway tests; 75 physical PostgreSQL tests (57 existing billing + 18 reconciliation, not a generation-success metric); 23 focused API tests; 11 migration tests. API mypy 277 source files, gateway mypy 34 source files; Ruff/diff checks green.
- Independent review P2 argument echo fixed; P3 order dependency fixed; bounded model-registration review no findings.
- Ruling: an unverified XLSX schema is not guessed. Ship a strict normalized operator boundary, then verify the actual export adapter when the source is available.
- Ruling: existing NULL organization cannot be backfilled from a coincident request ID. Such matches remain unresolved until authoritative account binding is supplied; no original receipt/wallet edits.
- Ruling: register ledger metadata to prevent a later autogeneration from treating these tables as unknown/drop candidates; database immutability is still migration-owned and physically tested.
- Additional physical proof: an already-settled customer keeps its balance, Usage and settlement across a mismatching ledger import/replay; the actual CLI imports/replays against owned PostgreSQL and writes mode-0600 reports.
- Production access: alias max-core DNS failure; existing Serverum console script timed out waiting for the actual core VM after normal SSO. Current environment has no configured outbound identities/secrets or TCP grants. No server command/deployment ran.
- Missing source: authorized balance-ledger operations with stable operation ID/ref_id/delta_kopecks and organization evidence. Payments XLSX is not that source.
- Next: commit/push and exact CI; obtain approved server route plus actual source, then canonical migration/API→gateway delivery and restricted real-operation verification. No completion claim until production acceptance.
