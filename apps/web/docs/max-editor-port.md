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

## Production rollout — 2026-09-07

Editor source: `aad48bdc89bae733af14183658ac79a61406f688`.
Deployed revision: `403e0c0fd3373dcf462656bb56ab3b74e887536c` (includes delivery notes).
Normal non-force push to `origin/main` succeeded after explicitly authorized,
temporary authentication. No supplied credential was saved in files or Git config.

The first SSH build disconnected before producing an image. A subsequent build
under the ordinary deployment user could not read root-owned source files and
failed typechecking. Git hashes verified those files matched the committed source.
Building the same production Dockerfile with privileged read access succeeded;
no file permissions or application code were changed to work around the issue.

Only `web` was recreated in `/opt/omnia/apps/llm-gateway/deploy/full`, using
`docker compose up -d --no-deps --no-build web` after the successful image build.
`WEB_IMAGE` and release identity were persisted in the existing production env;
other configuration was preserved. API, worker, generation-worker and gateway
container IDs/start times were identical before and after rollout.

- New image: `sha256:342f5a998d4253e01cb2d4b0d4d415daacb2074c0984fab330432443167f07af`.
- Container health: `healthy`.
- Public `/web-health`: `status: ok`, exact deployed revision above.
- Homepage and login: HTTP 200; anonymous MAX access retains the authentication redirect.
- Authenticated browser: editor, existing chat and version history loaded in two
  projects. Data points to the app tab. Publication and tools dialogs open and
  close with Escape, returning keyboard focus to their triggers.

Rollback revision: `1689a2380266215ed5ffb488c36297f83eef3004`.
Retained image: `sha256:c09841ea53be95128440bffcec96f0b29d52710ac6c9a9e9721d308c55f77a83`.
Protected on-host rollout record and previous env:
`/tmp/omnia-editor-release.77BFlp` (directory mode 700).
The rollout included automatic restoration on failed health checks; it was not needed.

## Remaining functional QA

No generation, publication or version restoration was triggered on the owner's
existing projects. In the inspected projects, one runtime rendered a 404 alongside
an old failed generation, and another remained synchronizing its saved version.
Those observations do not establish a frontend regression or successful runtime QA.
Real generation, historical-image selection and restoration still need a dedicated
test project. H131 remains testing and the separate functional-QA step stays open.
