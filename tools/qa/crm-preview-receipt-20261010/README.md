# CRM demo: reviewed product patch, not applied

This is a one-file candidate for project `2c7bb4d4-e661-45aa-8ad3-eb515838bd83`.
It changes only the generated product's `src/app/page.tsx`. An additive authenticated receipt lookup and trusted SDK/proxy update accompany
this candidate. Credentials, legal documents, database schema and bot bindings
are unchanged; no migration is required.
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
- The confirmation stores only key, a local payload fingerprint (or the server
  request digest for an adopted historical receipt) and confirmed deal ID in
  actor/origin-scoped session storage. F5 verifies a stored successful ID through
  authenticated `getYleumLeadStatus` before showing success.
- Double click has an immediate in-flight guard. A lost response retains the
  same intent key and permits replay only with the same payload. A subsequent
  rejection cannot erase an earlier unknown result. A confirmed success needs
  an explicit “Новая заявка” action before another key is created.
- An old global `lead_idempotency_key` is never deleted. The new read-only
  `getYleumLeadReceipt(key, expectations)` matches exactly one succeeded lead
  operation for the genuine MAX actor, project and current CRM connection/account.
  It returns only key, server digest, ID and generic detail status/warning; it
  performs no CRM request or write. A scoped adoption marker retains that exact
  historical identity and is revalidated on every mount. It permits an explicit
  new intent without overwriting a newer pending outcome. Unknown/rejected,
  multiple, malformed or mismatched receipts remain quarantined.
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
does not infer success for an unknown operation. The exact-key lookup confirms
only already succeeded receipts. Existing consent version `1`
and legal text remain unchanged.

## Verification on 10 October 2026

The old `99305c38` preparation bundle is superseded. Its green CI is not proof
for this new platform lookup/adoption implementation.

- Endpoint absence reproduced as a 404 before implementation. The real
  disposable PostgreSQL tests cover exact success, no provider I/O or writes,
  scope/account changes, nonsuccess states, malformed results, ID/digest
  mismatch, duplicate legacy rows and sanitized detail warnings.
- SDK/proxy: 17 failing probes before implementation, then 17 passed using the
  actual transpiled sources. Preview denial, signed actor/project/body binding,
  transport errors and absence of write/replay fallback are covered.
- Actual React page: 30 behavioral tests passed. The legacy adoption tests first
  reproduced four failures. Review identified marker changes and late scoped
  record changes at recovery/consent/rejection/new-intent boundaries; each was
  reproduced before adding identity comparisons. Repeat independent storage,
  authorization and privacy review: **No findings**.
- Full web suite: **116 files / 1053 tests passed**. Web lint/types, real product
  SDK/MAX UI types, Next build and the existing controller React effect-lifetime
  check passed for this exact candidate. Exact full CI is recorded in the PR
  handoff; suites overlap and must not be summed.
- Managed kit suite: **41 passed**, including physical PostgreSQL and the real
  portable starter bootstrap using pinned pnpm 9.15.0. An initial local run used
  the environment's newer pnpm wrapper and rejected the frozen lockfile; no
  dependency/lockfile change was made. A new assertion of the route literal was
  corrected to its actual rendered path.
- Kit v29 explicitly forces managed-file refresh. Its regression initially
  failed at v28; the test reads the emitted helper and proxy route, rather than
  assuming that a controller restart updates an existing product.

Run the behavioral suite from `apps/web`:

```sh
./node_modules/.bin/vitest run src/lib/__tests__/crm-product-patch.test.tsx
```

`CRM_PATCH_BASELINE=1` selects the supplied failing fixture. Do not use that
flag in regular CI. Mock component tests/local builds do not establish live
MAX, A/B CRM isolation, production preservation or publication.

## Safe application through the normal product pipeline

The Windows live-QA owner is the only cell/publication/deployment owner. The
current publication must finish and all relevant generation/publication leases
must be idle before considering application. This preparation does not authorize
an additional model call or external test write.

1. First the serial production owner must deploy the reviewed exact platform
   revision through canonical full delivery, rebuilding the trusted kit v29 and
   compiled managed core with matching pins on Core and Commerce. Verify the
   installed API/SDK/proxy and exact health/smoke. An owner-start alone does not
   refresh managed SDK source; normal generation preparation is required.
   API lookup needs genuine MAX auth and no new credential or CRM write.
2. Retrieve fresh complete draft metadata and `src/app/page.tsx` through the
   normal owner read path. Confirm project, owner, snapshot, current accepted
   commit and complete draft revision. Canonicalize the page as described above
   and compare its SHA-256 with `manifest.json`. Stop if the source has changed;
   do not overwrite an intervening edit or treat old metadata as a current CAS.
3. In the agreed window, provide this reviewed page to a bounded normal UI edit
   request restricted to `src/app/page.tsx`. Require the existing generation
   admission, workspace lease, coordinator finalization and source identity
   checks. Do not inject a draft and manufacture a completed run or permit.
   A model may alter the supplied candidate; exact acceptance must be checked,
   not assumed.
4. Require terminal `completed`, fresh full build/runtime/release proofs and the
   normal promotion permit. Read the resulting accepted snapshot and compare
   its page bytes with `after/src/app/page.tsx`; compare the rest of the complete
   tree with its baseline. Investigate every other changed path before accepting.
5. Confirm the new owner preview is read-only and has no CRM calls. Separately
   check the genuine MAX flow, returned deal ID, F5 verification, same-key replay
   and refreshed history, using only authorized synthetic records.
6. Publish that exact accepted revision through the ordinary UI and record
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

The ordinary QA account has an owner-approved audited temporary Pro window;
the live owner grants and restores it. This preparation does not mutate the
plan, wallet, quotas or roles. Require remaining normal edit quota at admission,
and restore Free after the authorized acceptance window.

## Platform release expectations

The sole Windows deployment owner uses the existing canonical production
path: `infra/release/deploy-prod.sh`, `infra/release/generation-drain.md`, and
both `apps/llm-gateway/deploy/full/docker-compose.yml` and
`docker-compose.hostdb.yml` with the existing project/env. Their verified pinned
Windows helper is the transport; do not use the obsolete SSH alias as proof.
This source slice does not change the gateway or production web app.

Require exact target source/image labels on API, worker, generation-worker,
Core/Commerce controllers and immutable billing; installed API schemas/router
and `MAX_MANAGED_KIT_VERSION=29`; rebuilt trusted template kit and compiled
core artifacts selected/pinned identically on both hosts. Obtain actual new
artifact digests during staging, not from this offline fixture. Verify
`CELL_PUBLIC_CORE_IMAGE` / `CELL_PREVIEW_CORE_IMAGE` through the existing
release process and record the actual pins. No new database migration,
credentials, grant, binding transfer, plan update or legal publication is part
of this platform release. Existing DB head 0082 must remain preserved.

Normal EDIT preparation refreshes these two platform-owned product paths:
`src/lib/omnia/integration-client.ts` and
`src/app/api/omnia/integrations/[...path]/route.ts`. Compare their resulting bytes
with the deployed trusted kit. The intended product change is only
`src/app/page.tsx`. Check the entire frozen baseline inventory after acceptance;
any other delta requires explanation and review. Never manufacture a permit,
completed run or snapshot from a draft save.

## Reported live baseline, not acceptance of this candidate

The Windows live-QA owner supplied these receipts on 10 October 2026:

- The ordinary QA owner published the existing accepted version through the
  normal UI; the public app returned HTTP 200 at
  `https://qa-10-10-publichnaia-amocrm-kabinet-b-9163ce.apps.yleum.ru`.
- The existing Coffee bot moved to this QA project after explicit owner and
  document approval. The old project/snapshots were retained; no token rotation.
- Genuine MAX validation rejected missing inputs/consent, then created the
  synthetic lead `2964609` in `officeyleumru`. A CRM UI stage change appeared
  in MAX history with a fresh server `checked_at`; reopening preserved history.
- Identical same-key replay returned `2964609`. Read-only operation checks before
  and after confirmed exactly one succeeded lead and one succeeded lead note;
  no duplicate operation was created.
- A fresh complete inventory had 44 files, accepted and draft identical. Its
  canonical page SHA-256 matched the baseline pinned in this manifest.

These are baseline receipts supplied by the live owner, not independent Linux
browser verification or evidence that this new preview/F5/adoption patch is
installed. The candidate remains unapplied. `yalium`/two-independent-CRM
negative isolation, candidate live acceptance and moderation resubmission
were not performed. No new moderation submission is part of this task.
