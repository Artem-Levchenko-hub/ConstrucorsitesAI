# CRM demo: reviewed product patch, not applied

This is a one-file candidate for project `2c7bb4d4-e661-45aa-8ad3-eb515838bd83`.
It changes only the generated product's `src/app/page.tsx`. Platform routes,
SDK, credentials, legal documents, database schema and bot bindings are unchanged.
Adding these fixtures to the platform repository does **not** install them in
the product, create an accepted product revision, or publish anything.

## Source provenance

`before/src/app/page.tsx` reconstructs the sanitized **draft supplied in chat**,
with HTML entities decoded, LF line endings and trailing whitespace removed.
It is not an independently retrieved immutable accepted file. The reported
source metadata was:

- Accepted commit: `ef0a03cb3d749c60464cbd801de66c7984169417`.
- Snapshot: `02cb9fcc-9d7f-46fa-a34f-4d7d7c5d36c9`.
- Draft revision: `9f152fd6624ffd1bba99f2de337f26c8df55c52070413bb4e342f1f5be07f8db`.
- Live version resolver: `ready`; the raw `queued` projection is not a defect.

`manifest.json` pins the fixture and patch bytes. `product.patch` is a valid
zero-context unified diff for the product path (offline `git apply` requires
`--unidiff-zero`). It must not be applied without checking the current complete
draft revision and comparing the current page to the normalized baseline.
The entire candidate is also readable in `after/src/app/page.tsx`.

## Behavior and boundaries

- The existing trusted `useMaxApp` preview mode renders a read-only form. It
  fetches app configuration but makes no CRM status, history, consent or create
  calls. Genuine MAX remains necessary for CRM data and writes. The managed
  route's authentication checks are not weakened.
- The confirmation stores only key, payload digest and confirmed deal ID in
  actor/origin-scoped session storage. F5 verifies a stored successful ID through
  authenticated `getYleumLeadStatus` before showing success.
- Double click has an immediate in-flight guard. A lost response retains the
  same intent key and permits replay only with the same payload. A subsequent
  rejection cannot erase an earlier unknown result. A confirmed success needs
  an explicit “Новая заявка” action before another key is created.
- Corrupt or inaccessible storage and unattributable legacy identities fail
  closed. No customer form payload or CRM history is stored in browser storage.
- Actor changes synchronously unmount private state. Old asynchronous replies
  cannot overwrite a later actor instance's storage or rendered history.
- History refreshes on tab entry, focus and visibility return, with a response
  sequence guard. Server `checked_at`, `has_more` and unconfirmed-details
  warnings remain visible; no push refresh is promised.

The scope assumes a distinct product origin; the operator must verify that
assumption. Session storage is per browser tab: cross-tab coordination and
two independent live CRM accounts were not tested. Recovery of an unknown
operation after F5 requires re-entering exactly the same payload; this patch
does not invent a server lookup by intent key. Existing consent version `1`
and legal text remain unchanged.

## Verification on 10 October 2026

- The initial eight behavioral tests against the supplied page reproduced seven
  failures and one pass. The final candidate passes 16/16 tests using its actual
  React component and mocked SDK/network/UIKit. These include stale A→B→A
  completion overwriting a newer unknown intent, malformed storage, double click,
  lost response/F5, typed 422, actor isolation, focus/visibility and detail warnings.
- The two independent-review defects were reproduced before fixing: late
  completion could overwrite storage; unconfirmed detail warnings were hidden.
  Repeat independent review: **No findings**, 16/16 tests passed.
- Full repository web suite: **116 files / 1039 tests passed**. The first full
  attempt exposed three asynchronous test timing failures; the harness now
  waits for actual dispatch/completion, and the full rerun passed.
- Full web ESLint and TypeScript passed. The product candidate separately passed
  real SDK/MAX UI types and `next build` on Next 15.5.24 / React 18.3.1 /
  MAX UI 0.2.0 in an owned disposable fixture. No provider or CRM network calls.

Run the behavioral suite from `apps/web`:

```sh
./node_modules/.bin/vitest run src/lib/__tests__/crm-product-patch.test.tsx
```

`CRM_PATCH_BASELINE=1` selects the supplied failing fixture. Do not use that
diagnostic flag in regular CI. Mock component tests and a local Next build do
not establish live MAX, A/B CRM isolation, production preservation or publication.

## Safe application through the normal product pipeline

The Windows live-QA owner is the only cell/publication/deployment owner. The
current publication must finish and all relevant generation/publication leases
must be idle before considering application. This preparation does not authorize
an additional model call or external test write.

1. Retrieve fresh complete draft metadata and `src/app/page.tsx` through the
   normal owner read path. Confirm project, owner, snapshot, current accepted
   commit and complete draft revision. Canonicalize the page as described above
   and compare its SHA-256 with `manifest.json`. Stop if the source has changed;
   do not overwrite an intervening edit or treat old metadata as a current CAS.
2. In the agreed window, provide this reviewed page to a bounded normal UI edit
   request restricted to `src/app/page.tsx`. Require the existing generation
   admission, workspace lease, coordinator finalization and source identity
   checks. Do not inject a draft and manufacture a completed run or permit.
   A model may alter the supplied candidate; exact acceptance must be checked,
   not assumed.
3. Require terminal `completed`, fresh full build/runtime/release proofs and the
   normal promotion permit. Read the resulting accepted snapshot and compare
   its page bytes with `after/src/app/page.tsx`; compare the rest of the complete
   tree with its baseline. Investigate every other changed path before accepting.
4. Confirm the new owner preview is read-only and has no CRM calls. Separately
   check the genuine MAX flow, returned deal ID, F5 verification, same-key replay
   and refreshed history, using only authorized synthetic records.
5. Publish that exact accepted revision through the ordinary UI and record
   active publication SHA/snapshot, release result and genuine MAX reopen checks.
   Until that receipt exists, label this candidate **not applied / not accepted /
   not published**.

Why a draft save is insufficient: `services/draft_changes.py:save_draft_as_version`
creates a manual commit/snapshot/version but runs no finalization. In contrast,
`services/generation/agent_publication.py` requires a promotion permit bound to
the current source identity, and `services/cell_publication.py` validates a
completed run, accepted candidate, proofs and snapshot binding. No existing
AI-free acceptance route for this changed source has been established here.
This patch does not add a bypass.
