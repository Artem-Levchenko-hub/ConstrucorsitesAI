# Bounded native source-repair inspection — 10 October 2026

## Confirmed failure and scope

The repair caller snapshots the failed source for change/acceptance checks, but
does not include those file contents in the model task. Previously, after two
discovery turns, both the advertised tools and executor rejected every read.
An offline reproduction exhausted the four-turn unchanged-source guard without
executing the first read of `src/app/page.tsx`.

The controlled regression first failed on the preceding implementation: only
the two discovery actions executed. With this change, one targeted read supplies
the existing source, a minimal edit changes it, and a fresh build precedes done.
No real provider calls or generated-app source mutations were used for this test.

## Changed contract

- The locked source-repair toolset can advertise one read of an unread existing
  source from the baseline snapshot, with exact path choices.
- Executor checks enforce the same limit, including multiple calls in one batch.
  An attempted read consumes the allowance even if it fails. Discovery reads
  count equivalent relative path spellings as the same file.
- Config, secret files, unknown paths and traversal are excluded from this new
  allowance. Existing project executor authorization remains mandatory.
- Reading does not count as implementation progress, reset the four-turn limit,
  create a green proof or authorize finalization. Shell/discovery loops remain
  blocked. Existing-edit write-only escalation is unchanged.
- Source-change, compiler, coordinator full/runtime/release, cancellation and
  deadline requirements remain in place. No migration or operator setting changes.

## Live evidence boundaries and delivery

The coordinating browser QA reported two failed initial generations in the same
new project: `e2a65ca4-e58f-49aa-a1d1-529484c6373b` and
`38153dde-f4a1-4df7-b3cb-372c6cf073b5`. Neither accepted a new candidate; publication,
bot switching and genuine MAX data acceptance were not performed in those runs.
Read blocking was observed after capability rejection. The offline test proves
the policy defect; it does not prove it was the sole cause of either live failure.

Profile was reported genuinely missing in the first failed draft; its history
match was not confirmed as a detector defect. The second failed draft's complete
47-file manifest was reported to contain no `src/app/page.tsx` or alternative root
page: its UI was genuinely absent. The capability detector is unchanged. Reported
generated checkout atomicity, invalid-item pricing and audit writes to an absent
legacy table are separate unaccepted draft defects, not repaired by this change.

This document is a source handoff, not a deployment receipt. Exact CI/review and
production identity/health/end receipts belong in the accompanying PR. Production
activation remains serial under the coordinating Windows owner, after a fresh
global idle check, canonical drain/backup/release/verify/end gates. Reuse the tracked
`infra/release/deploy-prod.sh` and existing approved full-compose phase helpers.
Until that receipt, the reported production baseline is `649ac122`; real repair
and new-project MAX E2E acceptance remain NOT RUN for this change.
