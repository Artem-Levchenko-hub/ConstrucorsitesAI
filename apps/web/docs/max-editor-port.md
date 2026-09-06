# MAX editor port — 2026-09-07

## Scope

Port of the approved local MAX Studio prototype editor onto production source
`1689a2380266215ed5ffb488c36297f83eef3004`. Not a replacement of the runtime
with prototype mocks. No API, auth, payment, database or deployment changes.

- Light editor-only tokens and a full-width header; other routes retain their theme.
- One publication entry opens the existing real readiness/launch panel.
- Application data links to `/settings?tab=app`.
- Projects, navigation and account live in a keyboard-accessible drawer.
- Chat and preview are separate working areas; narrow screens mount the preview
  only while its dialog is open. History ownership, stale-HEAD safeguards,
  rollback, stream/cancel/queue and protected preview sessions remain intact.
- No empty history rail before the first version. Failed history remains retryable.
- Contextual advice stays available behind an explicit disclosure control.

## Local verification

Baseline: 270 tests. New checks cover empty/error history, one header launch entry,
data destination, mobile preview mount count, Escape/focus restoration and contrast.
277 tests, TypeScript and ESLint passed. Independent review findings were fixed.
The preview animation test now waits for the visible state instead of a fixed
60 ms delay; delayed animation frames are covered without changing runtime code.
Browser checks used 1280×720, 390×844 and 320×568. The composer and primary actions
fit the viewport; no horizontal overflow was observed.

The localhost browser check used existing development mock mode and a temporary
font fixture outside the repository. Server-backed history/session endpoints were
unavailable there; their safeguards are covered by the existing regression suite.
This is not evidence of a successful live generation or production deployment.

After network access was approved, all 277 tests, TypeScript and ESLint passed
again. A normal optimized Next build with `NEXT_PUBLIC_USE_MOCKS=false`, real
fonts and no font fixture completed successfully (46 static pages).

## Release gate — still pending

Editor source committed locally as `aad48bdc89bae733af14183658ac79a61406f688`.
The normal non-force push `git push origin HEAD:refs/heads/main` failed with
`fatal: could not read Username for 'https://github.com': Device not configured`.
No production write or container restart was attempted. GitHub write authentication
must be configured before resuming the push and rollout; do not send secrets in chat.

Network access is approved. Upstream and the live web health revision both match
`1689a2380266215ed5ffb488c36297f83eef3004` before rollout. The live web image is
`sha256:c09841ea53be95128440bffcec96f0b29d52710ac6c9a9e9721d308c55f77a83`.
Production Git HEAD/index are root-owned; privileged read-only inspection verified
the revision and unrelated secondbrain changes, which must be preserved.
Do not label this change deployed until the remaining gate is complete:

1. Fetch current upstream and inspect concurrent changes; preserve the user's
   divergent local main and other worktrees.
2. Re-run tests, typecheck, lint and a standard production build with real fonts
   and `NEXT_PUBLIC_USE_MOCKS=false`.
3. Push the reviewed revision using the normal non-force workflow.
4. Record the currently deployed web revision/image for rollback. Deploy only
   `web` through the documented `/opt/omnia/apps/llm-gateway/deploy/full` stack.
5. Verify deployed revision, web health, authenticated MAX editor, real generation,
   selected historical image, rollback and protected live preview.
6. Mark H131 and V8 complete only after those checks; no new DB migrations.
