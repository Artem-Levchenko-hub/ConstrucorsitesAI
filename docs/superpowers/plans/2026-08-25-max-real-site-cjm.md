# MAX Real-Site CJM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать автономный интерактивный HTML, который визуально копирует актуальный сайт Omnia и проводит пользователя по идеальному MAX-пути от знакомства и регистрации до Free/Pro/Business, договора, первой генерации, публикации и управления.

**Architecture:** Один self-contained HTML реализует CJM-shell из референса и реальные визуальные паттерны `apps/web` через конечный автомат из 17 экранов. Все данные и действия остаются локальными; один Sol-владелец последовательно реализует интерфейс и поведение, а независимый reviewer проверяет спецификацию и регрессии.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, inline SVG, PowerShell/Node smoke checks, in-app Browser/Playwright-compatible browser verification.

**Spec:** `docs/superpowers/specs/2026-08-25-max-real-site-cjm-design.md`

## Global Constraints

- Create `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html` and do not modify `omnia-max-cjm (3).html` or `omnia-max-cjm-client.html`.
- Exactly 17 touchpoints, 7 phases, 3 plans, and at least 18 named scenarios.
- Match the current warm Omnia system from `apps/web/src/app/globals.css`: page `#f5f3ee`, paper `#fcfbf7`, ink `#171716`, border `#d8d4cb`, coral `#f15a38`, success `#248a4b`, error `#c63d35`.
- Reproduce actual patterns from BrandMark, PublicPageShell, pricing, MAX registration/onboarding, MaxWorkspaceShell, MaxLivePreview, LegalPage, and AccountControlCenter.
- Keep notes customer-facing; do not expose APIs, servers, databases, LLMs, source code, or deployment state in the ordinary journey.
- Optimize for fast, ethical conversion: show the phone result before registration, one decision and one dominant CTA per screen, no more than five required brief questions, recommendation only after personalized summary, and no paid-plan preselection, fake scarcity, countdown, guilt copy, or hidden limits.
- Add restrained, non-blocking delight at plan activation, first usable preview, and verified publication; respect reduced motion.
- Free is 0 ₽ with no card and no renewal controls. Pro is 1 490 ₽/month. Business is 4 990 ₽/month and visibly marked as a dated demo catalog snapshot.
- Offer acceptance, data consent, marketing, and paid auto-renewal are separate. Marketing and renewal begin unchecked; Free never shows renewal.
- Contract acceptance precedes activation and every generation path. Direct late-step navigation must show an honest prerequisite gate.
- The file is a local UX demonstration and must not register, charge, accept a contract, generate, publish, or send network requests.
- Verify 1440×900, 1024×768, 768×1024, 390×844, keyboard navigation, 200% text, reduced motion, and zero horizontal overflow.
- Preserve user-owned `AGENTS.md`, `.codex-migration-backups/`, `.codex-tmp/`, and `tmp/`.

---

### Task 1: Visual shell, overview map, and executable state skeleton

**Files:**
- Create: `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`
- Create (scratch, ignored): `.superpowers/sdd/2026-08-25-max-real-site-cjm/smoke.ps1`
- Create (scratch, ignored): `.superpowers/sdd/2026-08-25-max-real-site-cjm/report.md`
- Reference: `C:\Users\79133\Downloads\omnia-max-cjm (3).html`
- Reference: `apps/web/src/app/globals.css`
- Reference: `apps/web/src/components/BrandMark.tsx`
- Reference: `apps/web/src/components/ui/button.tsx`
- Reference: `apps/web/src/app/page.tsx`

**Interfaces:**
- Produces constants `PHASES`, `TOUCHPOINTS`, `PLANS`, `SCENARIOS`.
- Produces `createInitialState(): JourneyState`, `renderApp(): void`, `renderOverview(): string`, `openTouchpoint(index: number): void`, `resetDemo(): void`.
- Produces DOM roots `#omnia-cjm-app`, `#screen-root`, `#notes-root`, and `#live-region` for Tasks 2–4.

- [ ] **Step 1: Write a failing structural smoke test**

Create `smoke.ps1` with a Task 1 check that fails while the target is missing and later checks exact public interfaces:

```powershell
$ErrorActionPreference = 'Stop'
$target = 'C:\Users\79133\Downloads\omnia-max-cjm-real-site.html'
if (-not (Test-Path -LiteralPath $target)) { throw 'Target HTML is missing' }
$html = Get-Content -Raw -LiteralPath $target

if (([regex]::Matches($html, "id:\s*'step-[0-9]+'" )).Count -ne 17) {
  throw 'Expected exactly 17 touchpoints'
}
if (([regex]::Matches($html, "name:\s*'[^']+'\s*,\s*short:" )).Count -ne 7) {
  throw 'Expected exactly 7 phases'
}
foreach ($needle in @(
  'const PLANS', 'const SCENARIOS', 'function createInitialState',
  'function renderApp', 'function renderOverview', 'function openTouchpoint',
  'id="omnia-cjm-app"', 'id="live-region"'
)) {
  if (-not $html.Contains($needle)) { throw "Missing $needle" }
}
'PASS task-1 shell'
```

- [ ] **Step 2: Run the smoke test and record RED**

Run:

```powershell
& '.\.superpowers\sdd\2026-08-25-max-real-site-cjm\smoke.ps1'
```

Expected: exit `1` with `Target HTML is missing`.

- [ ] **Step 3: Implement the autonomous document and Omnia tokens**

Create one HTML with no external assets. Define CSS custom properties and the real shell:

```css
:root {
  --page: #f5f3ee;
  --paper: #fcfbf7;
  --surface: #ffffff;
  --muted-surface: #ece8df;
  --ink: #171716;
  --ink-2: #6d6962;
  --ink-3: #8d887f;
  --line: #d8d4cb;
  --line-soft: #e7e3da;
  --coral: #f15a38;
  --coral-hover: #d94929;
  --success: #248a4b;
  --danger: #c63d35;
  --radius-control: 8px;
  --radius-card: 12px;
}
```

Use a 64 px header, inline BrandMark SVG, graphite hero, paper cards, coral primary buttons, 44 px controls, mono kickers, phone preview, visible focus, reduced motion, and a responsive sticky bottom bar.

- [ ] **Step 4: Define exact data and initial state**

The JavaScript must expose these stable shapes:

```js
const PHASES = [
  { id: 'value', name: 'Ценность и вход', short: 'Вход' },
  { id: 'account', name: 'Аккаунт и заказчик', short: 'Аккаунт' },
  { id: 'task', name: 'Задача и рекомендация', short: 'Задача' },
  { id: 'contract', name: 'Договор и активация', short: 'Договор' },
  { id: 'creation', name: 'Первая генерация', short: 'Создание' },
  { id: 'publish', name: 'Публикация', short: 'Запуск' },
  { id: 'manage', name: 'После запуска', short: 'Управление' },
];

const PLANS = {
  free: { name: 'Free', price: 0, renewalAvailable: false },
  pro: { name: 'Pro', price: 1490, renewalAvailable: true },
  business: { name: 'Business', price: 4990, renewalAvailable: true },
};

function createInitialState() {
  return {
    view: 'overview', currentIndex: 0, phaseFilter: 'all', notesOpen: false,
    notesTab: 'user', scenario: 'fresh',
    account: { email: 'owner@example.com', passwordValid: false, emailVerified: false },
    buyer: { kind: 'org', name: '', inn: '', representative: '', authority: false, confirmed: false, snapshot: null },
    brief: { business: '', audience: '', action: '', materials: 'partial', launchDate: '', saved: false, summaryApproved: false },
    plan: { key: null, catalogVersion: '25.08.2026', selectedAt: null },
    contract: { offerVersion: '25.08.2026', offer: false, data: false, marketing: false, renewal: false, accepted: false, snapshot: null },
    activation: { status: 'inactive', periodEnd: null, receipt: false },
    generation: { status: 'idle', stage: 0, version: 0, reviewed: false, error: null },
    publication: { status: 'idle', readiness: {}, url: '', lastReadyVersion: 0, error: null },
    management: { renewal: false, paymentPermission: false, cancellation: 'none', refund: 'none' },
    message: '', focusTarget: null,
  };
}
```

- [ ] **Step 5: Render overview and sequential chrome**

Implement `TOUCHPOINTS` with exact ids `step-1` through `step-17`, phase ids, titles, one-line promises, results, conditions, recoveries, and emotions. Render the hero, five counters, seven phase cards, seventeen touchpoint cards, phase filter, scenario selector, notes button, and Back/Next navigation.

The first two screens must sell the outcome before requesting account data: a graphite Omnia hero, realistic phone preview, a business-example switcher, and one concrete CTA. Notes explain the conversion rationale without technical language.

- [ ] **Step 6: Run Task 1 smoke to GREEN**

Run the same PowerShell command. Expected: `PASS task-1 shell` and exit `0`.

- [ ] **Step 7: Record Task 1 evidence**

Append exact target hash, size, and RED/GREEN output to `report.md`. Do not commit the external HTML or scratch files.

---

### Task 2: Registration, buyer, plan, contract, and activation gates

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`
- Modify: `.superpowers/sdd/2026-08-25-max-real-site-cjm/smoke.ps1`
- Modify: `.superpowers/sdd/2026-08-25-max-real-site-cjm/report.md`
- Reference: `apps/web/src/components/auth/AuthCard.tsx`
- Reference: `apps/web/src/app/(auth)/max/register/page.tsx`
- Reference: `apps/web/src/components/max/MaxRegisterForm.tsx`
- Reference: `apps/web/src/components/max/MaxOnboarding.tsx`
- Reference: `apps/web/src/app/pricing/page.tsx`
- Reference: `apps/web/src/components/legal/LegalPage.tsx`

**Interfaces:**
- Consumes Task 1 `JourneyState`, plans, renderer, and touchpoint navigation.
- Produces `hasVerifiedAccount()`, `hasConfirmedBuyer()`, `contractSnapshotMatches()`, `canAcceptContract()`, `canActivate()`, `invalidateDependentState(reason)`, `firstMissingPrerequisite(index)`.
- Produces renderers `renderLanding`, `renderExample`, `renderRegistration`, `renderEmail`, `renderBuyer`, `renderBrief`, `renderSummary`, `renderPlans`, `renderOrder`, `renderContract`, `renderActivation`.

- [ ] **Step 1: Extend the smoke test with failing behavior assertions**

Extract the inline script into a Node `vm` after removing DOM bootstrap, provide minimal DOM stubs, and assert:

```js
assert.equal(ctx.hasVerifiedAccount(), false);
assert.equal(ctx.canAcceptContract(), false);
ctx.state.account.passwordValid = true;
ctx.state.account.emailVerified = true;
ctx.state.buyer = { kind:'org', name:'ООО «Ладога»', inn:'7812345678', representative:'Анна Петрова', authority:true, confirmed:true, snapshot:'buyer-v1' };
ctx.state.brief.saved = true;
ctx.state.brief.summaryApproved = true;
ctx.state.plan.key = 'pro';
ctx.state.contract.offer = true;
ctx.state.contract.data = true;
assert.equal(ctx.canAcceptContract(), true);
ctx.acceptContract();
assert.equal(ctx.canActivate(), true);
ctx.changeBuyerField('name', 'ООО «Новый заказчик»');
ctx.changeBuyerField('name', 'ООО «Изменённый заказчик»');
assert.equal(ctx.state.plan.key, 'pro');
assert.equal(ctx.state.contract.accepted, false);
assert.match(ctx.state.message, /Повторно подтвердите/);
```

Also assert Free hides renewal, paid renewal starts false, and payment failure → Free requires a fresh contract.

- [ ] **Step 2: Run behavior checks and record RED**

Expected: exit `1` because gate functions and handlers do not yet exist.

- [ ] **Step 3: Implement account and buyer screens**

Registration must use the actual two-column MAX pattern, inline validation, password visibility, and one primary CTA. Email confirmation includes change/resend/expired states. Buyer onboarding uses three progress tiles and separate authority confirmation for organizations.

- [ ] **Step 4: Implement brief, summary, and plan selection**

Persist every form field through re-renders. Validate only on submission and focus the first invalid field. Render three equal pricing cards, no preselection, one explained recommendation, exact dated prices, and a single CTA after selection.

Keep registration to email/password and the brief to five required questions. Show plan guidance only after the personalized summary is approved; never interrupt setup with a paywall or countdown.

- [ ] **Step 5: Implement snapshot-based gates**

Use stable serialized snapshots:

```js
function buyerSnapshot() {
  const b = state.buyer;
  return JSON.stringify([b.kind, b.name.trim(), b.inn.trim(), b.representative.trim(), b.authority]);
}

function contractSnapshot() {
  return JSON.stringify([buyerSnapshot(), state.plan.key, state.contract.offerVersion]);
}

function contractSnapshotMatches() {
  return state.contract.accepted && state.contract.snapshot === contractSnapshot();
}
```

Buyer, authority, plan, or offer-version changes invalidate only dependent acceptance/activation/generation/publication state, preserve the brief and plan where applicable, and keep the explanatory message until explicit reconfirmation.

- [ ] **Step 6: Implement order, contract, and activation screens**

Order summary shows all material terms. Contract renders separate unchecked controls. Free activation has no card or renewal. Paid activation shows one period and optional renewal state. Every action is visibly marked as a demonstration.

- [ ] **Step 7: Run Task 1–2 smoke to GREEN**

Expected: shell and contract behavior pass, including multi-input warning persistence and payment → Free fresh acceptance.

- [ ] **Step 8: Record Task 2 evidence**

Append RED/GREEN output and exact gates exercised to `report.md`.

---

### Task 3: Workspace, generation, publication, management, notes, and scenarios

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`
- Modify: `.superpowers/sdd/2026-08-25-max-real-site-cjm/smoke.ps1`
- Modify: `.superpowers/sdd/2026-08-25-max-real-site-cjm/report.md`
- Reference: `apps/web/src/components/max/MaxWorkspaceShell.tsx`
- Reference: `apps/web/src/components/max/MaxProjectNav.tsx`
- Reference: `apps/web/src/components/max/MaxSectionShell.tsx`
- Reference: `apps/web/src/components/max/MaxLivePreview.tsx`

**Interfaces:**
- Consumes Task 2 activation and snapshot gates.
- Produces `canGenerate()`, `canReview()`, `canPublish()`, `startGeneration()`, `advanceGeneration()`, `retryGeneration()`, `publishProject()`, `retryPublication()`, `rollbackPublication()`, `applyScenario(key)`, `restoreFocus()`.
- Produces renderers `renderGenerationBrief`, `renderGeneration`, `renderReview`, `renderReadiness`, `renderPublication`, `renderManagement`, `renderNotes`.

- [ ] **Step 1: Add failing journey assertions**

Assert protected late steps do not invent state, generation has four readable stages, review requires a ready generation, publication requires reviewed readiness, rollback preserves the prior ready version, and management differs for Free versus paid plans.

Assert `Object.keys(SCENARIOS).length >= 18` and exercise every named scenario through `applyScenario` without exceptions.

- [ ] **Step 2: Run journey checks and record RED**

Expected: exit `1` because later renderers and transition functions are missing.

- [ ] **Step 3: Build the faithful workspace and phone preview**

Render a 64 px header, 220 px project navigation, central chat/brief surface, optional 380–420 px live-preview rail, black phone frame, status bar, connected dot, version rail, and restore action. Collapse navigation and preview into drawers below desktop.

- [ ] **Step 4: Implement generation, review, and correction**

Generation moves through four customer-readable stages and supports safe close, retry, return to brief, and support. Review shows version history, rights/material explanation, checklist, correction box, and a single “Подготовить к публикации” CTA.

Add non-blocking success states at activation, first preview, and publication. Each celebration must expose the next productive action immediately and disappear under reduced motion.

- [ ] **Step 5: Implement readiness, publication, and rollback**

Readiness items link to their exact recovery step. Publication progresses through preparing/publishing/checking/ready. Failure exposes retry and rollback; success exposes the public demo URL and next action.

- [ ] **Step 6: Implement plan-aware management**

Free shows plan limits and upgrade/support only. Paid shows period end, exact next charge or no charge, independent renewal and payment-permission controls, receipts/contracts, plan change, cancellation/refund, export, and support.

- [ ] **Step 7: Implement notes and scenario switching**

Notes have `user`, `why`, `terms`, and `recovery` tabs. Desktop uses a side panel; mobile uses a context-preserving bottom sheet. Scenario changes update local state only, announce the new state, and route to the scenario’s relevant touchpoint.

- [ ] **Step 8: Implement focus and persistence**

Persist a versioned state subset to `localStorage`. Restore focus only when the requested control exists, is enabled, and becomes `document.activeElement`; otherwise focus `#screen-title[tabindex="-1"]`.

- [ ] **Step 9: Run all smoke checks to GREEN**

Expected output:

```text
PASS task-1 shell
PASS task-2 account, plans, contract and activation
PASS task-3 generation, publication, management, notes and scenarios
PASS all MAX real-site CJM smoke checks
```

- [ ] **Step 10: Record Task 3 evidence**

Append state-transition coverage, final hash, and original-reference hashes to `report.md`.

---

### Task 4: Browser verification and independent review

**Files:**
- Verify: `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`
- Modify: `.superpowers/sdd/2026-08-25-max-real-site-cjm/report.md`
- Create (scratch): `.superpowers/sdd/2026-08-25-max-real-site-cjm/review-package.md`

**Interfaces:**
- Consumes the complete HTML and smoke suite.
- Produces verified browser evidence and an independent Critical/Important review verdict.

- [ ] **Step 1: Serve only the target artifact locally**

Start a localhost server bound to `127.0.0.1`; restrict the handler to the target filename and return 404 for other paths.

- [ ] **Step 2: Run three complete happy paths**

Browser-click every step for:

1. Free — no payment method, no renewal UI, contract before activation/generation.
2. Pro — exact 1 490 ₽, no renewal, receipt confirmation, complete generation and publication.
3. Business — exact 4 990 ₽, separately enabled renewal with visible next date/amount.

- [ ] **Step 3: Run recoverable-error paths**

Exercise expired email, invalid buyer, missing authority, updated offer, missing mandatory consent, failed payment, payment → Free, incomplete brief, generation error, correction, missing publication prerequisite, publication retry/rollback, and renewal/payment-permission withdrawal.

- [ ] **Step 4: Verify visual fidelity and responsive layouts**

Capture overview plus all 17 touchpoints at 1440×900 and 390×844; capture representative registration, pricing, contract, workspace, generation, publication, and management screens at 1024×768 and 768×1024. Compare BrandMark, header height, palette, cards, buttons, typography, phone frame, and workspace proportions against the named production components.

- [ ] **Step 5: Verify accessibility and runtime isolation**

Check keyboard order, visible focus, programmatic labels, accessible names, selected state beyond color, `aria-live`, 200% text, reduced motion, no positive `tabindex`, no horizontal overflow, zero external requests, and zero browser exceptions.

- [ ] **Step 6: Request independent review**

Provide the reviewer the spec, plan, final hash, test commands, and artifact. Require exact line evidence and verdicts for visual fidelity, all gates, Free semantics, paid renewal, direct navigation, form persistence, notes, error recovery, focus, accessibility, and absence of invented legal facts.

- [ ] **Step 7: Fix every Critical/Important finding test-first**

For each finding, extend smoke/browser checks to RED, make one minimal fix, rerun to GREEN, and return the same reviewer for scoped re-review until all findings are addressed or a genuine external blocker is documented.

- [ ] **Step 8: Stop local servers and finalize evidence**

Record final SHA-256, test outputs, screenshot locations, reviewer verdict, and unchanged reference hashes in `report.md`.

---

### Task 5: Live report and delivery

**Files:**
- Modify: `otchet/data.json`
- Commit: `docs/superpowers/plans/2026-08-25-max-real-site-cjm.md`
- External deliverable: `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`

**Interfaces:**
- Consumes final verified artifact evidence.
- Produces a truthful H117 testing record and completed V8 step.

- [ ] **Step 1: Update H117 after verified implementation**

Change H117 from `open` to `testing`, keep `score: null` until real-user conversion evidence exists, replace evidence with final hash and exact smoke/browser/review results, and explain the demonstrated UX impact without claiming measured conversion.

- [ ] **Step 2: Complete the V8 CJM step and version the report**

Set `CJM MAX в дизайне реального сайта...` to `true` only when Tasks 1–4 are green. Increment `meta.version` and keep `meta.updated` current.

- [ ] **Step 3: Verify report and repository diff**

Run:

```powershell
Get-Content -Raw otchet/data.json | ConvertFrom-Json | Out-Null
git diff --check
git status --short --branch
```

Serve `otchet/` locally and require HTTP 200 for `/` and `/data.json` with the new version and H117 status.

- [ ] **Step 4: Commit only intended repository files**

Stage the plan and `otchet/data.json`; preserve `AGENTS.md` and unrelated untracked paths. Use:

```text
docs(max): deliver real-site interactive CJM

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

- [ ] **Step 5: Push, publish the static report, and verify production**

Push `main` to its configured origin. Fast-forward production to the pushed revision, copy only the new `otchet/data.json` to `/var/www/otchet/` with a backup, and verify:

- production revision equals the pushed commit;
- `omnia-prod-web` is healthy;
- public `/web-health`, `/otchet/`, and `/otchet/data.json` return 200;
- public report shows the expected version, H117=`testing`, artifact hash, and completed V8 step.

- [ ] **Step 6: Deliver the local artifact**

Return a clickable absolute link to `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`, state that both references remain unchanged, summarize verified routes, and disclose that production catalog/legal details still require owner and lawyer confirmation.
