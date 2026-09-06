# MAX Studio remaining frontend implementation plan

> Use subagent-driven-development in this session. One implementation agent at a time; task review after each. The user has approved the prototype and requested all remaining screens immediately.

**Spec:** `docs/superpowers/specs/2026-09-07-max-studio-production-port.md`

**Goal:** Complete the light MAX-only frontend journey around the already deployed editor and deliver to existing production.

**Architecture:** Keep Next.js routes, React Query/API hooks and existing server actions. Introduce scoped MAX light tokens and focused view components, not an HTML iframe or prototype runtime. No new dependency is required. Prototype source is `/Users/romanisakin/Documents/Codex/2026-08-30/new-chat-2/outputs/max-studio-cjm`; production app is `apps/web`.

## Global Constraints

- Preserve real authentication, API contracts, authorization, generation, preview, version restore, deployment, credentials, payment and subscription logic.
- Never copy prototype simulated mutations, fake analytics, hardcoded payment prices or local success flags into production.
- MAX-only public/Studio journey; preserve unrelated legacy routes and backend code.
- Scoped light styling, readable text, one clear primary action, compact rows instead of nested bubble cards, functional mobile navigation and keyboard-accessible dialogs.
- No paid/generative/destructive actions on real user projects during QA. No backend changes, force pushes or unrelated source changes. Controller alone pushes/deploys the web service.
- Use apply_patch for edits. Follow TDD for behavior changes. Use existing Vitest patterns; report RED and GREEN evidence. Commit intended task files only, with per-command `git -c user.name=Codex -c user.email=noreply@openai.com commit` if identity is absent.

### Task 1: Shared MAX visual foundation and project entry

**Files:** create `apps/web/src/components/max/max-studio.css`, `MaxProjectWizard.tsx` and focused tests; modify `MaxStudio.tsx`, `MaxStudioProjectCard.tsx`, `MaxStudioHeader.tsx`, `MaxStudioAccountDisclosure.tsx` as needed; notification provider in `apps/web/src/components/providers.tsx` or actual provider file discovered by `rg Toaster`; corresponding tests. Do not edit launch/account/landing implementations owned by later tasks.

1. Inspect prototype `projects.css`, `app.js` guided creation, `notifications.js/css`, production MaxStudio, card, header, providers and existing editor tokens.
2. Add failing interaction tests: project search remains usable for no results with clear action; loading/error/empty are distinct; wizard Next/Back retains answers, validates required name/idea, and does not call create until final review; pending final submit prevents duplicate create.
3. Create reusable scoped `[data-max-studio]` semantic tokens matching editor light colors (foreground #192335, secondary #526079, base #f7f8fb, surface white, primary #2563eb). Keep legacy dark scope unchanged. Export no business logic from CSS. Explicitly scope portaled dialog content too.
4. Replace single-item left navigation and decorative project tiles with compact projects workspace: brand/header/account/help; heading and primary New application; searchable compact grid/list with clear status and existing next-action routing. Preserve actual project query/create, credential redaction/connect logic, `saveMaxProjectConfig`, starter sessionStorage and routing from MaxStudio. Do not claim no projects on query failure or a filtered no-match.
5. Extract controlled wizard UI from MaxStudio: idea/name → audience/main action → features/style → review; fields map exactly to existing state/config. Back retains answers; final invokes unchanged parent mutation. Optional fields must stay optional, defaults unchanged. Focus step heading on navigation, retain close/Escape via existing accessible dialog. Pending submit locks close/repeated submit appropriately.
6. Configure existing Sonner host top-right, close button, semantic readable light theme, visible cap 3, default 8 seconds and error persistence (via existing toast wrapper or host behavior where supported without changing every call). Motion/reduced-motion and keyboard focus must remain supported. Do not add route-change spam or invent background steps.
7. Run focused tests, full tests once, typecheck, lint. Self-review and commit. Report new CSS contract to controller for downstream tasks.

### Task 2: MAX-only landing, authentication and onboarding

**Files:** `apps/web/src/app/page.tsx`, public `src/app/max/product/page.tsx`, `max/start/page.tsx`, `max/guide/page.tsx`, `src/components/max/guide/GuideVisuals.tsx`; `src/components/marketing/PublicPageShell.tsx`, public about/contact/changelog/security/pricing/mvp/requisites and legal presentation shells; `src/components/auth/AuthCard.tsx`, LoginForm/RegisterForm/PasswordRecoveryForm/PasswordResetForm only as needed for styling/journey; `src/components/max/MaxRegisterForm.tsx`, `VerifyMaxEmail.tsx`, `MaxOnboarding.tsx`; supporting focused marketing component/CSS and tests.

1. Inspect prototype landing.css/landing markup and existing routes/server actions. Reuse Task 1 `[data-max-studio]` tokens and import CSS on each needed route boundary. Do not modify auth API endpoints or server business checks.
2. Add failing rendering/routing tests that public landing contains only MAX proposition and authenticated entry path, no ordinary builder choices, no guest direct editor CTA. Auth redirects must be local/safe and preserve existing return semantics; forms retain errors/loading states.
3. Build approved light landing with informative labelled idea → preview example, concise benefits/how it works/FAQ, one create MAX app CTA plus login; examples are explicitly illustrations, not evidence of live projects or unsupported provider capability. Replace old Hero/planner updates and multi-product section. `/max/product` should share this MAX entry, not remain contradictory.
4. Port login/register/recovery/reset and MAX registration/email verification/onboarding to consistent light split layout with clear primary action, visible labels, validation and consent. Preserve actual server actions, OAuth/email verification redirects, onboarding fields/business verification and authorization. Do not make registration appear to succeed locally or skip legal consent.
   Public help/start/guide and informational/legals should share the light hierarchy too. Preserve legal document contents; change presentation only. Remove obsolete multi-product claims from marketing copy, not historical changelog records. Remove ordinary-product navigation; legacy `/apps`, `/web-apps`, `/landings` may redirect to `/max/product` instead of advertising unavailable products. Public pricing must use existing public payment config prices or link to actual account pricing without hardcoded unsupported prices/benefits. Guide illustrations must be labelled examples and maintain their useful navigation/callouts.
5. Reuse existing shared components where possible; no new global dark overrides or duplicate landing logic. Run focused tests, full suite once, typecheck/lint; self-review and commit.

### Task 3: Project data, launch, integrations and management

**Files:** `apps/web/src/components/max/MaxSectionShell.tsx`, `MaxProjectSetupDialog.tsx`, `MaxSettingsWorkspace.tsx`, `MaxPublishWorkspace.tsx`, `MaxLaunchPanel.tsx`, `MaxLaunchButton.tsx`, `MaxPostLaunchDashboard.tsx`, `src/components/integrations/FigmaIntegrationHub.tsx` (resolve actual path), MAX integration/VPS dialog components reached from these views if they still use hardcoded dark styles; focused tests and a project-workspace CSS if needed.

1. Read prototype builder.js/guidance.js/secondary.js and existing readiness/publication-state helpers, launch modal, settings tabs, integration hub/runtime dependencies. Keep real queries, permissions, confirmations, deployment polling/current-version checks.
2. Add failing tests for visible optional integrations/server actions in launch; correct server-derived published/checking/failed/outdated states; loading/error not misrepresented as healthy; settings tab selection and accessible close; management empty/error history distinct.
3. Replace bulky dark section shell with compact light project header/navigation, stable editor/data/launch/management routes, primary launch accessible above the fold. Data modal has distinct tabs, section headings, readable inputs, compact fixed action footer and viewport-safe scrolling. Existing form values/save/version behavior stays real.
4. Port integration catalog/search/category/connection and VPS panels with real connection status, compact rows and appropriate actions. Never auto-connect or expose secrets; keep all credential handling and masked displays.
5. Consolidate launch presentation around current MaxLaunchPanel/MaxLaunchButton so duplicate large legacy publication cards do not compete. Main next action first; show readiness totals with discoverable details; optional services/server rows visible without hidden generic accordion. Retain active deployment feedback and errors; success has compact URL+copy, primary open application, outlined management/update actions and proper responsive dialog sizing. Do not infer current publication from an older completed deploy.
6. Dashboard uses asymmetrical status/actions + concise real operational metrics (no visitors API exists, so do not fabricate chart/numbers); below, clear health/details and publication history. Translate technical labels, expose query retry, do not label unknown health as success. Keep runtime controls/history real and route to editor/settings as appropriate.
   Distinguish runtime/preview activity from publication: production preflight has `runtime.state=running` for an unpublished project. Do not label that published or live for users. A successful past deploy is publication evidence, not a fresh continuous uptime/health measurement; label only what each API actually proves.
7. Run focused tests, full suite once, typecheck/lint; self-review and commit. Identify untouched shared dark dialog risks explicitly.

### Task 4: Account sections and real payment journey

**Files:** `apps/web/src/components/account/AccountShell.tsx`, `AccountControlCenter.tsx`; new focused `AccountBilling.tsx`, `PaymentCheckout.tsx`, `AccountSecurity.tsx`, `AccountProfile.tsx`, `account.css` as necessary to keep responsibilities separate; `src/app/(app)/billing/*` and `src/lib/api/account.ts` only if needed for existing-contract idempotency; focused tests.

**Confirmed contract:** top-up provider returns to `/account?payment=<id>` (currently profile page), subscription returns to `/billing/plan?payment=<id>`. Handle both, preserving the query during any route redirect. `getWallet` already exists at `src/lib/api/wallet.ts`. `listPayments` returns up to 100 account-scoped payments. Existing backend also exposes POST `/api/payments/{id}/reconcile`, but passive listing/webhook confirmation is enough for initial UI; do not invent polling mutations. Report unknown/unavailable status honestly. Real organization update API exists (`saveBusinessProfile`); current onboarding only confirms email, so organization form must not send users to a dead-end onboarding loop if editing is needed.

1. Read prototype account.css/secondary.js payment flow and existing server billing return route, API PaymentConfig/Payment/Subscription types, balance source and auth-session API. Do not invent unsupported endpoints. Reuse Task 1 tokens, with explicit scope on portals.
2. Add failing tests for choosing package opens review before POST; package amounts/credit come from server; explicit pay creates once, disabled while pending; returned redirect alone is not success; pending/cancelled/succeeded/unknown/error render distinctly using listPayments; disabled provider cannot checkout; autoRenew consent defaults false. Also test six-section navigation on mobile, current session/error states and destructive confirmation.
3. Structure all six account sections, not one generic pile: profile identity/export with compact delete confirmation; organization real business review state and onboarding action; security device/session rows+errors; balance prominent real wallet value and packages; operations filterable ledger with real statuses/date/amount; current plan and comparison from real entitlements, explicit renewal consent and cancellation/restoration actions.
4. Implement payment review dialog/page containing selected server price, credited amount, one-off vs monthly distinction, offer link and clear YooKassa redirect action. On response use only backend confirmation_url; show recoverable error when missing. Retain payment id for return lookup only; poll/refetch real listPayments for status and invalidate balance/subscription only upon confirmed success. Never mark paid from URL or local storage. Keep unknown/pending state and safe retry without blindly duplicating an unresolved payment; use existing backend idempotency capability when extending API function signatures.
   Preserve the payment query across a guest login round-trip on both return routes, accounting for the outer authenticated layout as well as the page. Existing middleware does not yet guard account/billing prefixes; a page-level redirect alone may be bypassed by the layout. Keep login's existing stale-cookie protection. Persist an uncertain checkout's idempotency key and selection together so reopening the same attempt cannot silently create a duplicate.
5. Payment UI may say provider unavailable per PaymentConfig.reason; do not fake an embedded YooKassa card form. Preserve legal consent/version, actual billing amounts and backend validation. No live payment during QA.
6. Run focused tests, full suite once, typecheck/lint; self-review and commit.

### Task 5: Whole journey QA, delivery record and frontend rollout

**Files:** focused regressions in `apps/web`; `apps/web/docs/max-studio-port.md`; `otchet/data.json` and project delivery notes per `otchet/README.md`.

1. Review the combined task reports and source diff against spec; fix concrete cross-section regressions found during browser walkthrough. Do not reopen the approved design or change backend scope.
2. Verify full Vitest suite, TypeScript, ESLint, production Next build with NEXT_PUBLIC_USE_MOCKS=false, diff whitespace, report JSON/schema sanity.
3. Browser QA anonymous landing/auth, local safe mocked fixtures or signed-in read-only projects/settings/launch/account at desktop and mobile. Check visible hierarchy, no horizontal overflow, native keyboard/Escape/focus and links. No payments/generation/deletion/publication on user projects.
4. Write truthful source-to-route coverage and QA limits. Update delivery report with new change entry/version, no invented success metrics; commit.
5. Controller: review whole branch, then normal push HEAD:main and frontend-only deployment using documented full compose at `/opt/omnia/apps/llm-gateway/deploy/full`. Sudo git/docker build needed for root-owned source. Preserve unrelated server changes and current non-web container IDs; rollback web on failed health. Confirm public `/web-health` revision, endpoint statuses and web health. Publish report alias `/var/www/otchet/data.json` after source deployment.
