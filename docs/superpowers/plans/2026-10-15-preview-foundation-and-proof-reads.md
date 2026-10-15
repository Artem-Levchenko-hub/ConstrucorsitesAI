# Browser preview foundation and proof reads implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Recover owner preview without MAX, keep a renewable standalone owner shell, and remove redundant proof reads without changing acceptance decisions.

**Architecture:** Extend the existing owner capability protocol and project authorization. The stable Yleum page remains the parent of the application frame, including in a separate tab. Initial private-boundary failures use trusted recovery HTML; they never expose product HTML before authentication. Pure proof-loading optimization is independently reviewable.

**Tech Stack:** Existing Next.js 15.5.24, React 19, TanStack Query 5, FastAPI, PostgreSQL and portable machine boundary. Use pnpm 9.15.0 and the frozen lockfile. No dependency upgrades.

**Spec:** `docs/superpowers/specs/2026-10-15-browser-preview-refactor-design.md`

## Global constraints

- No paid model calls. No production/customer data removal or credential disclosure.
- Keep normal MAX authentication, owner authorization, private/public boundary separation and business-write non-replay.
- Preserve the existing renewal messages `omnia:preview-session:renew` / `omnia:preview-session:result`, exact source/origin checks and 32-character hexadecimal nonce.
- A signed owner bootstrap URL is not a guest link. Guest access is not enabled by this foundation package.
- Candidate/accepted version pinning and isolated guest runtimes remain subsequent packages; never label the mutable draft URL as immutable accepted preview.
- Only the coordinator commits/pushes/deploys. Workers edit their named files and report test evidence.

## Review focus

1. A portable boundary may reject HTML before React runs: Task 3 must cover this separately.
2. An actual rejected MAX launch must never fall back to owner preview: Task 2 tests this.
3. A stale project/frame or spoofed renewal response must not receive capability access: Tasks 1–3 retain exact activity/source/origin/nonce guards.
4. A recovery or cancellation must not replay a business POST or lose an already mounted draft form: Tasks 1–2 test this.
5. Reusable proof receipts can belong to an earlier source identity: Task 4 preserves dimension-key lookup and forged/stale rejection.

### Task 1: Stable owner page and shared preview history

**Files:** Create `apps/web/src/app/(app)/max/[id]/preview/page.tsx`, `apps/web/src/components/max/MaxOwnerPreviewPage.tsx`, `apps/web/src/lib/use-max-preview-history.ts`; modify `MaxWorkspaceShell.tsx`, `MaxLivePreview.tsx`; behavioral web tests.

**Interfaces:** `useMaxPreviewHistory(project: Project)` returns currentSnapshotId, versions, historyCurrent and the existing history/snapshots query results. The stable page calls `loadMaxProject(id, nextPath)` after normal session admission, then renders the owner-only preview shell. Separate opening navigates to `/max/{id}/preview`, not a short-lived capability URL.

- [x] Add failing route admission, separate-open and shared history tests; no model/runtime publication call is allowed by navigation itself.
- [x] Run the new tests against the old code and record expected failures.
- [x] Extract the existing history computation without changing query keys, freshness checks, pagination or restoration consumers; implement the stable owner shell with a continuously mounted live frame and return-to-editor action.
- [x] Preserve the current editor/dialog behavior and source-identity cancellation. Add a controlled capability-renewal channel owned by the shell, not the popup opener.
- [x] Run web preview/session/history/navigation regressions, typecheck and lint. Independently review and commit this task only.

### Task 2: Initial owner authentication recovery

**Files:** Modify `apps/orchestrator/templates/max-miniapp-nextjs/src/lib/max/owner-preview-renewal.ts`, `src/components/MaxAppProvider.tsx`; test `apps/web/src/lib/__tests__/owner-preview-renewal.test.ts` and `max-app-provider-session.test.tsx`.

**Interfaces:** Export `recoverOwnerPreviewSession(boundary: Window, configuredOrigins?: readonly string[]): Promise<boolean>`. It uses the existing trusted parent protocol, bootstrap JSON and server session proof. Existing `installOwnerPreviewFetch` reuses the same session recovery implementation and keeps its cleanup contract.

- [x] Add RED tests: initial GET 401 in an authorized owner iframe can recover, while standalone/no-parent, forged origin/source/nonce, rejected MAX initData and invalid server identity cannot grant access.
- [x] Implement bounded recovery without installing a global preview fetch guard before server proof. The provider may re-read its session after recovery; it must not replay any business call.
- [x] Keep mounted form preservation, single-flight renewal, abort/cleanup and exact response validation. Unknown/503 failures remain visible and bounded.
- [x] Run both test files plus existing preview bootstrap/session tests, relevant type checks, independent review and a scoped commit.

### Task 3: Trusted private boundary recovery before product HTML

**Files:** Modify `apps/orchestrator/src/yleum_orchestrator/services/machine_boundary.py`; create a focused recovery helper module only if needed; extend `apps/orchestrator/tests/test_machine_boundary.py`.

**Interfaces:** For an unauthenticated private preview document GET, return only a trusted recovery shell. It requests the existing parent capability and bootstraps it on the same private origin. Authenticated requests preserve existing forwarding. Public MAX login/bootstrap and the ban on public preview-session access remain unchanged.

- [x] Add RED HTTP tests for a private document without cookie, parent recovery, API/non-document requests, forged origins, public mode and canonical request-path attacks.
- [x] Serve recovery HTML only for safe document navigation. Embed only validated configured editor origins; use an exact source/origin/nonce bridge, bounded timeout, strict CSP and no-store/no-referrer headers. Never include a reusable credential or product HTML before server authentication.
- [x] Confirm the server session before reloading the original relative document path. No business request is retried. A top-level private URL displays a neutral link to the canonical owner shell, never an instruction to install MAX.
- [ ] Finish Linux owner-preview orchestrator regressions in exact-revision CI. Local boundary checks (35), focused types/lint, independent review and scoped commit are complete; Windows full collection is blocked by missing dependencies.

### Task 4: Proof receipts without duplicate database reads

**Files:** Modify `apps/api/src/yleum_api/services/max_finalization.py`, optionally `project_cell_proofs.py` for a narrowly typed batch lookup; extend `apps/api/tests/test_max_finalization_progress.py` and focused receipt-loading tests.

**Interfaces:** Keep existing external finalization APIs and proof lookup semantics. Loader results may be passed explicitly to `_load_bundle`; no process-global cache. Target the measured green path: first remove duplicate bootstrap read (7 to 6 reads), then batch compatible dimension lookups if the same decisions can be proven (target 3 total reads including proof row).

- [x] Freeze complete permit/rejection behavior for current, reusable, stale, missing and forged receipts; add an independently counted DB-call assertion that fails on the old repeated read path.
- [x] Implement only loading/assembly changes, preserving dimension-key identity, release/source binding, transaction/repair ordering and errors. Do not replace lookup with proof_id-only matching.
- [ ] Finish full exact-revision CI with disposable PostgreSQL. Focused finalization/proof checks (57 + 58), all frozen rejections, independent SQL count 7 to 6, lint and types are complete.
- [x] Independently review and commit the task. No claimed latency improvement without a comparable timing measurement.

## Subsequent packages and delivery

After this foundation, continue with immutable accepted artifact serving and isolated candidate DBs; then revocable interactive guest runtimes/datasets with server egress denial; then native file-delta and continuation reducers. Each gets its own exact interface plan when the preceding contract is verified. These requirements remain open until their acceptance matrix passes; the foundation is not completion of the full feature.

For each accepted package, save a scoped commit to GitHub, run exact-revision CI and use only the documented production release path when production access and required safe migration evidence are available. Keep one checkpoint with current GitHub/live revisions, test evidence and the next unresolved acceptance case. User has delegated routine implementation decisions; do not pause for further plan approval.

## Verified foundation checkpoint

Tasks 1–4 implemented and independently reviewed. Full web baseline: 123 suites / 1,144 tests passed; final auth changes: 41 tests passed plus fresh typecheck and lint. Local commits: b4d12f7b (proof reads), cc3b08a9 (startup session), 84a60dd3 (trusted boundary), b0242ba3 (stable owner page/history). Matching GitHub trees are verified during connector publication; remote commit IDs may differ because the connector authors commits. Root retains local history without rewrite.

No model calls, schema migrations, real business writes or bot changes. Production SSH currently times out during banner exchange; Serverum reserve session shows login. Public API health remains on 06d11e1d. Delivery and live acceptance stay open until exact CI and documented production release complete. Do not start unrelated changes before resuming that delivery loop.
