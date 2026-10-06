# LLMGW ledger reconciliation — 6 October 2026

This change adds an operator reconciliation mechanism, not an automatic provider
API client or a new customer charge. Production reconciliation is not accepted
until an authorized balance-ledger source and the actual deployment are verified.

## Verified starting state

- Source base: `09a9d8a4b2cf80b40b0754c0debbb9ec4430524b` (main checked this session).
- Existing `provider_calls` is independent of customer Usage/settlements/wallets.
- Existing cost reports distinguish reported RUB, estimates and unknown values.
- The latest locally available independently parsed production smoke observed
  API/Web/workers `09a9d8a4` at 06 October 03:03:03 UTC. This is dated evidence,
  not a fresh server check for this change; that smoke does not expose gateway SHA.
- This host's approved `max-core` alias fails DNS resolution. Public health from
  this execution environment returned observer/upstream 503; no current revision
  was obtained. This does not by itself establish a universal production outage.
- No authoritative balance-ledger file is present in this workspace. The user's
  three payment XLSX files contain top-ups and YooKassa IDs, not request expenses.

The user observed ledger and export URLs in the authorized dashboard. Their
existence is not an authenticated API contract. No browser cookie/token is
extracted and no endpoint block is bypassed by this implementation.

## Data contract

An operator first verifies the real source's columns and organization. The
adapter must preserve the stable operation ID, exact `ref_id` and integer
`delta_kopecks`. Do not normalize screenshots, displayed times or rounded totals
into fabricated provider IDs. Source authenticity must be verified separately:
a SHA256 proves byte identity only, not who issued a file.

The CLI accepts a bounded normalized JSON alongside the original source file:

```json
{
  "schema_version": 1,
  "organization_id": "VERIFIED_PROVIDER_ORGANIZATION",
  "source_kind": "balance_ledger_export",
  "source_sha256": "SHA256_OF_ORIGINAL_SOURCE_BYTES",
  "operations": [
    {
      "operation_id": "STABLE_PROVIDER_OPERATION_ID",
      "ref_id": "EXACT_PROVIDER_REQUEST_ID_OR_NULL",
      "kind": "usage",
      "delta_kopecks": -2398,
      "currency": "RUB"
    }
  ]
}
```

The placeholders are a contract example, not real provider evidence. `kind`
supports `usage`, `correction`, `other`; classification must come from verified
provider meaning, not inferred from amount/time. Other operations remain
separate and are excluded from expense confirmation. Currency must be RUB.
`authorized_ledger_api` is a second source label only, not a promise that an API
route is accessible. Conflicting JSON keys, booleans/fractional/string amounts,
wrong source hash and foreign expected organization are rejected before DB I/O.

## Storage and matching

Migration `0080_provider_ledger` adds immutable entries, conflict observations and
call confirmations. UPDATE/DELETE/TRUNCATE are blocked by database triggers.
Confirmation insertion verifies call organization, exact ref_id and original
receipt hashes in the database. No migration backfill or historical repricing.

New call admissions record operator-verified `LLMGW_ORGANIZATION_ID`. It is
non-secret and optional; empty/unset leaves scope unknown. The environment value
must be verified against the actual configured provider credential's account.
Do not set it from a guessed alias. Existing NULL-scope calls cannot be silently
backfilled; their authoritative organization binding remains a separate evidence
gap. This prevents importing a foreign account's statement merely because an ID
matches. No new user-facing endpoint is introduced.

Matching is organization + exact ref_id only. There is no fallback to time,
model, token counts or amount. Missing refs, unknown calls/organizations,
incomplete receipts, changed receipts, conflicting operation copies and duplicate
usage refs remain unresolved. Confirmations are append-only; if later evidence
introduces a conflict or duplicate ref, the current report excludes the affected
previous confirmation from confirmed totals, retaining its historical record.

Corrections have their own operation IDs and signed amounts; they never rewrite
an earlier charge. Confirmed expenses are the negative sum of integer deltas.
The report separately compares linked original estimates using Decimal without
pretending that estimates were reported-RUB response receipts. Provider costs
also remain distinct from what a customer owes under the success-only policy.
If only a correction is included for a call, the estimate comparison is unknown:
the correction is not compared with the full cost of a missing original charge.

## Operator execution

Use the API environment after migration; credentials come from its existing
authorized DB configuration, never from command-line browser tokens:

```bash
uv run --frozen python scripts/reconcile_provider_ledger.py \
  --expected-organization-id VERIFIED_PROVIDER_ORGANIZATION \
  --normalized-statement /restricted/verified-ledger.normalized.json \
  --source /restricted/original-balance-ledger.xlsx \
  --report-file /restricted/reconciliation-new.json
```

Reports are created exclusively with mode 0600 and do not overwrite existing
files or symlink targets. Stdout contains aggregate counts and amounts only;
request/call/operation IDs stay in the restricted report. Exit 0 means no
unresolved expense entries in the imported organization, 2 means import committed
but unresolved entries remain, 3 means a safe coded error. Import does not
guarantee the source covers all provider operations. A report file write failure
can occur after the immutable import commits; rerun to a new report path safely.
`--report-only` reads the organization without importing.

## Delivery order and remaining acceptance

1. Verify CI and independent review; back up the actual production DB/config.
2. Canonical `/opt/omnia/apps/llm-gateway/deploy/full`, project `full`: migrate
   API to 0080 before activating the new gateway admission writer. Preserve
   unrelated compose/env and service versions. Verify exact image labels/health.
3. Verify the provider organization using authorized evidence, then configure
   that non-secret identity. Leaving it unset cannot establish reconciliation.
4. Obtain the real balance-ledger export/API result through an allowed source;
   verify its actual schema. Do not treat payments.xlsx as expense evidence.
5. Import and inspect linked IDs in a restricted report, integer totals,
   unresolved entries, untouched original receipts and unchanged customer wallets.
6. Replay the same source; confirm no duplicate confirmations or customer debit.

Rollback keeps migration 0080 and its immutable evidence. Do not downgrade/drop
the ledger tables in production to roll back service images. This change requires
no new model generation, provider call, payment or publication for its tests.

Historical 31 unmatched operations / 150.41 RUB are received prior findings,
not reverified or automatically allocated by this work. Six UI amounts summing
44.55 RUB alone do not establish six request-level matches. Exact production
acceptance remains blocked while source/organization/server access are absent.
