# Remaining MAX Studio frontend port

Baseline source: `7e3af9d5eb58502558f0a337574e63a0c3536b97`. Baseline deployed editor/web: `403e0c0fd3373dcf462656bb56ab3b74e887536c`.

Approved prototype: `outputs/max-studio-cjm` in the parent project workspace. Its local flags, simulated transactions and demonstration statistics are not production contracts.

## Source-to-route coverage ledger

| Area | Routes | Production source | Migration / evidence |
|---|---|---|---|
| Projects and creation | `/max` | `MaxStudio.tsx`, `MaxStudioProjectCard.tsx`, `MaxProjectWizard.tsx`, `MaxStudioHeader.tsx`, `max-studio.css` | `0ac026f9`: compact list, search recovery, controlled four-step wizard and account entry. Task review approved; local desktop/mobile 390px check covered Next/Back retention and Escape without final submission. |
| Notifications | Shared Sonner host | `app/providers.tsx`, `app/notifications.css` | `0ac026f9`: top-right, close controls, semantic colors, eight-second ordinary notices, persistent errors and same-ID recovery. Timer regressions cover expiry and error recovery. |
| Public and help | `/`, `/max/product`, `/max/start`, `/max/guide`, public information and legal shells | `MaxPublicLanding.tsx`, `max-public.css`, `PublicPageShell.tsx`, `LegalPage.tsx`, route pages | `e2d54a33`, `95aed59a`, `7fc0c49b`: shared MAX-only light landing, labelled examples, help/legal presentation and session-aware actual-pricing entry. Task review approved after bounded return-path fixes; desktop/mobile landing and guide checked. |
| Authentication | `/login`, `/register`, `/max/register`, `/forgot-password`, `/reset-password`, `/max/verify-email`, `/max/onboarding` | `AuthCard.tsx`, auth form components, MAX registration/verification/onboarding components, auth route pages | Same Task 2 commits: light split form layout, mobile form-first, bare generic registration enters the existing MAX consent flow and explicit legacy return/provenance flows remain. Guest registration was checked at desktop/390px without submission. |
| Editor | `/max/[id]` | Existing `MaxEditorLayout.tsx` and editor/runtime components | Previous release `403e0c0f`; this migration does not replace generation, version, streaming or preview logic. Read-only production preflight loaded an existing project preview. |
| Data and integrations | `/max/[id]/settings?tab=app\|bot\|vps`, `/max/[id]/integrations`, data dialog | `MaxProjectSetupDialog.tsx`, `MaxSettingsWorkspace.tsx`, `FigmaIntegrationHub.tsx`, `MaxIntegrationButton.tsx`, `ExternalDeployWizard.tsx`, `max-project-workspace.css` | `b3848646` plus Task 5 QA: light compact navigation, four-tab data dialog, searchable service catalog and scoped credential/VPS panels. Read-only fixtures covered populated/error states; final QA associates all VPS fields with labels and wraps the four data tabs into a visible 2×2 grid at 320/390px. No connection or save was submitted. |
| Publication and management | `/max/[id]/publish`, `/max/[id]/dashboard`, editor launch panel | `MaxLaunchPanel.tsx`, `MaxLaunchButton.tsx`, `MaxPublishWorkspace.tsx`, `MaxPostLaunchDashboard.tsx`, publication-state helpers | `b3848646`, `d27fbdba`: one shared launch panel, visible optional services/server actions, current-version publication, explicit loading/error/history states and distinct preview runtime. Review approved after the deferred-deploy regression fix. Published and unavailable fixture states were inspected; no traffic or uptime metrics were invented. |
| Account and checkout | `/account`, `/account/organization`, `/account/security`, `/billing`, `/billing/transactions`, `/billing/plan` | `AccountShell.tsx`, focused `Account*` components, `PaymentCheckout.tsx`, `account.css`, account API and auth-return boundaries | `55ff3787`, `ad155298`, `1215aa7b`: six distinct responsive sections, real wallet/payment/plan data, review before payment POST, durable idempotency intent and server-confirmed return status. Mobile operation rows expose amount/status. Task review approved after definitive-rejection recovery was narrowed to actual backend error codes. No live payment, deletion or session revocation was performed. |

Unrelated legacy editor/research routes and admin authorization are preserved, not advertised as alternatives to MAX Studio. Legal bodies and merchant configuration were not changed by this visual port.

## Browser QA boundaries

The local browser used `NEXT_PUBLIC_USE_MOCKS=true` with a loopback read-only fixture API on port 4311. That API accepted only GET/OPTIONS and returned 405 for every mutation; it did not forward requests. Checks covered desktop and 390px projects/wizard, landing, guest registration, login, guide/legal shell, populated/error data modal, publication/management states, credential dialog, all six account sections, payment review and pending/succeeded return fixtures. Final QA at 390×844 and 320×568 confirmed all four data tabs remain visible; End selects and focuses «Политики», Escape closes the dialog, and document width matches the 320px viewport. VPS controls expose the names «Домен», «Публичный IP VPS», «SSH-пользователь», «SSH-порт» and «Пароль SSH». No input or form was submitted.

Fixture results prove presentation and client-side state handling, not provider operation or production success. Production payment configuration was read-only and disabled with «Платежи включатся после подключения магазина ЮKassa»; real server prices were 490/1490/3990 ₽ at preflight. Configuring ЮKassa or executing a payment is outside this rollout.

## Verification ledger

- Task 1: 49 test files / 286 tests, TypeScript, ESLint and whitespace checks passed.
- Task 2: 50 files / 288 tests before review fixes; final focused 4/4 plus TypeScript/ESLint passed. A task-level production build passed after network access for `next/font`.
- Task 3: 52 files / 304 tests; final review-fix focus 26/26 plus TypeScript/ESLint/whitespace passed.
- Task 4: 54 files / 334 tests before bounded review fixes; final focused 38/38 plus TypeScript/ESLint/whitespace passed.
- Task 5 pre-rollout: VPS accessible-label RED failed on the missing `htmlFor`; GREEN and related project/data/deploy tests passed 21/21. TypeScript and ESLint passed.
- Final style integration review: focused RED reproduced low-contrast pricing text/icon and the missing admin dark boundary. GREEN plus related public/account/theme tests passed 52/52; TypeScript and ESLint passed. Browser QA confirmed the pricing CTA is white on blue at rest and populated synthetic admin users/audit remain readable without mutations; hover is covered by the computed CSS regression, not claimed as a browser interaction.
- Final exact runtime revision `21bd589ad977d2bf5be4fe87d33e7bb233914358`: 346/346 tests in 56 files, TypeScript, ESLint and whitespace checks passed. Production build with `NEXT_PUBLIC_USE_MOCKS=false` generated 46/46 pages. Whole-branch review and scoped re-review approved with no unresolved findings.

## Production rollout record

- Deployed on 2026-09-07: normal push to `origin/main`, server fetch plus fast-forward, direct web-image build and web-only rollout using compose project `full` at `/opt/omnia/apps/llm-gateway/deploy/full`.
- Runtime revision/image: `21bd589ad977d2bf5be4fe87d33e7bb233914358` / `omnia-web:21bd589ad977d2bf5be4fe87d33e7bb233914358`. Docker image ID: `sha256:8513fcdefa3658568febec22d423ed265d04b2a05ae3ec471c67e0d0e19db6ba`.
- Public `/web-health` returned `status: ok` and the exact runtime revision; web container is healthy. `/`, `/max/product`, `/login`, `/max/register`, `/pricing`, `/max/guide` and `/legal/terms` returned HTTP 200. Guest `/max` redirected to MAX registration; both payment-return routes redirected to login while retaining `next` and the payment query.
- API, worker, generation-worker and gateway container IDs and StartedAt were identical before/after rollout. Previous web image and protected environment backup remain available in `/tmp/omnia-max-studio-release.G70K3e` on the host. Unrelated server changes were preserved.
- Signed-in production browser confirmed the new management layout, unpublished state and empty publication history from real server data; publication showed 2/6 required checks and the next incomplete step; balance showed actual server packages and disabled checkout with the merchant-configuration explanation. No mutation was submitted.
- This post-delivery documentation/report update does not change runtime code or require another web rebuild. The report JSON is published separately to `/var/www/otchet/data.json` after its source commit.

The production source has unrelated server changes, so delivery must use fetch plus fast-forward merge without reset, force push or permission changes. Build with `NEXT_PUBLIC_USE_MOCKS=false`; retain the previous web image/environment for rollback and do not restart non-web services.

## Remaining functional QA limits

Real registration/email delivery, provider checkout, generation, historical-image restore, credential connection, external VPS provisioning and publication were deliberately not exercised on user projects. The signed-in read-only production pass and exact post-rollout health/revision checks passed; they do not substitute for these functional checks. The final MAX Partner handoff still requires a real moderated bot and publication URL; this frontend rollout does not claim that external step is complete.
