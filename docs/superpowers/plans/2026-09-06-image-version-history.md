# Image Version History Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to maintain and verify this plan task by task. The interactive historical-runtime experiment is superseded; do not reinstate it.

**Goal:** Let owners browse permanent, accepted-message versions through saved images without starting old applications or changing the current project.

**Architecture:** Durable `ProjectVersion` rows describe user-visible history independently of technical snapshots. Exact-source capture publishes immutable image artifacts during generation; authorized image GETs are the only preview work caused by history navigation. React Query paginates version metadata, while the viewer displays images and preserves explicit selection across background HEAD changes.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, object storage, the existing screenshot runner, React/Next.js, TanStack Query, Vitest.

**Spec:** User PDF `Da_Optimalnaya_skhema_listaem_izobrazhen_20260906_171510.pdf`, supplied 2026-09-06. The requirements below are the durable repository record of that PDF; the original local attachment is `/Users/romanisakin/Downloads/Da_Optimalnaya_skhema_listaem_izobrazhen_20260906_171510.pdf`.

## Constraints

- One accepted message creates one permanent version number, including queued, failed, cancelled and unchanged outcomes. Duplicate delivery/retry must not create another version.
- Technical starter/sync/retry snapshots belong to their source message/run. Neither prompt-text heuristics nor array length may define versions or their numbers.
- Unchanged versions keep their own number and reference the earlier result; do not duplicate source or images unnecessarily.
- Clicking any version, including the current one, selects images. Only an explicit “Живое превью” action leaves image history.
- Selection, arrows, swipes and prefetch must not start generation, builds, a Project Cell, session minting, runtime synchronization, rollback, database changes or integration writes.
- A missing/failed image must remain visibly missing/failed. Never substitute current HEAD or another version's image.
- Full source, exact dependency files/locks and migrations must remain archived independently of screenshots. Images alone cannot restore an application.
- MAX rollback is deliberately unavailable: `can_restore=false` and the backend rejects the operation before changing HEAD. Do not return a false successful response. Safe compatibility checks and atomic activation are a separate future prerequisite.
- Future confirmed rollback must create a new version, preserve existing history and business data, and keep the published application serving while preparation occurs. It must never blindly rewind orders/users or swallow activation failures.

## 1. Durable versions and owner-scoped API

**Files:** `apps/api/migrations/versions/0060_project_versions.py`, `models/project_version.py`, `schemas/project_version.py`, `services/project_versions.py`, `routers/project_versions.py`, generation acceptance/finalization in `routers/messages.py`.

- [x] Introduce permanent project-local version numbers and message/run lineage; group technical snapshots by explicit lineage.
- [x] Return `GET /api/projects/{project_id}/versions?before=<number>&limit=30` as `{versions, next_cursor}` ordered newest first. The cursor is exclusive; there is no global 30-version cutoff.
- [x] Expose version ID/number/status, nullable result snapshot/commit, preview status/artifacts, `is_current` and `can_restore`. Applied current state is separate from the newest queued/failed message.
- [ ] Finish parent-owned API/migration validation against the integrated branch. Required regression file: `apps/api/tests/test_project_versions.py` (accepted-message identity, stable numbering, lineage, lifecycle, pagination, ownership and MAX restore gate).

## 2. Exact immutable image capture

**Files:** `apps/api/src/omnia_api/services/snapshot_preview_capture.py`, `workers/preview.py`, `routers/snapshots.py`, snapshot model and generation finalization; parent-owned screenshot runner.

- [x] Bind capture to saved source while the generation lease still protects that source. Compare source before and after rendering; publish no image if the comparison fails.
- [x] Persist capture metadata/artifact identity for the exact snapshot; do not enqueue a later screenshot of whatever happens to occupy the current container.
- [x] Serve image bytes through owner-authorized immutable artifact GETs. Technical snapshots without a new image must not erase the source version's image.
- [ ] Complete parent-owned archived-source reconstruction/backfill. Use an isolated renderer and preview-only fixture data, never production mutations. Mark these artifacts `reconstructed: true`.
- [ ] Finish capture/ownership proof tests: `test_snapshot_preview_capture.py`, `test_snapshot_preview_images.py`. Verify an old image remains tied to its source after a later generation changes HEAD.

## 3. Image history UI

**Files:** `apps/web/src/components/max/{MaxWorkspaceShell,MaxVersionRail,MaxLivePreview}.tsx`, `components/workspace/{VersionImagePreview,PreviewFrame}.tsx`, `lib/api/{types,snapshots}.ts`, `lib/project-version.ts`, `hooks/usePromptStream.ts`.

- [x] Use server version IDs/numbers in MAX history; show thumbnail, description, date and lifecycle. Load earlier pages explicitly with `useInfiniteQuery`; retry the failed older cursor rather than merely refetching the first page.
- [x] Refresh version metadata after message acceptance and phase/completion/failure/cancellation events; retain bounded metadata polling as reconnect/event-loss fallback.
- [x] Preserve version selection across new HEAD events. Clear it only on explicit return, successful permitted restore, or project change; A → B → A must not resurrect selection.
- [x] Display full-height images with vertical scrolling. Provide scoped arrow keys, previous/next buttons, horizontal-dominant swipes and adjacent immutable-image prefetch. Different captured widths on the same route have distinct selector labels.
- [x] Resolve backend `/api/` image URLs through the configured API origin in image, thumbnail and prefetch paths.
- [x] Show “Восстановлено из кода · данные для предпросмотра” only for the currently displayed reconstructed artifact.
- [x] Disable live queries/startup/recovery during history and cancel scheduled connection retries. Historical missing/unresolved IDs never enable a live iframe. The generic builder uses snapshot images with the same viewer.
- [x] Keep rollback behind explicit confirmation and the server capability gate; pass the selected result `snapshot_id`, never the version ID.

## 4. Proof and delivery

- [x] Write meaningful React regressions in `apps/web/src/lib/__tests__/image-version-history.test.tsx`, including >30 permanent numbers, failed-page retry, rapid selection/late image events, scrolling/swiping, missing images, selected current version, prefetch, no runtime calls, HEAD events, A → B → A, explicit restore, status refresh and reconstructed-image labeling.
- [x] Preserve current-live recovery tests in `max-live-preview-recovery.test.tsx`. Remove obsolete source-string assertions and unused prompt-based numbering/grouping code.
- [x] Run web verification: `pnpm --dir apps/web test`, `pnpm --dir apps/web typecheck`, ESLint for changed web files and `git diff --check`.
- [ ] Parent runs the integrated API/capture/restore suites and runner checks.
- [ ] Parent reviews, commits, pushes and deploys the integrated revision through the documented production path, then verifies health and image-history behavior. Subagents do not commit or deploy.
- [ ] Production browser proof: select old/current version images and navigate without runtime/build/session/rollback requests; confirm tall images scroll, old numbers remain stable, older pages remain reachable, missing images are honest and MAX restore is unavailable.

**Current evidence (2026-09-06):** Full web suite: 47 files / 258 tests passed, including 19 image-history React tests. TypeScript, affected-file ESLint and diff check passed. Cursor retry, terminal-status refresh, split-origin image URLs and reconstructed-image labeling each have RED → GREEN regression evidence. API/runner and production evidence belong to the parent delivery report; this document does not claim deployment.
