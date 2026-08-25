# MAX Real-Site CJM — Design Specification

## 1. Purpose

Create a standalone interactive customer-journey prototype that looks and behaves as if the user is inside the real Omnia MAX constructor. The prototype must demonstrate the ideal end-to-end path from registration to a Free, Pro, or Business subscription, contract acceptance, first generation, publication, and subscription management.

The result is not a technical architecture map. It is a product experience that owners can click through while contextual notes explain why every step exists, what the customer understands, and how the path recovers from errors.

## 2. Deliverables

- New standalone artifact: `C:\Users\79133\Downloads\omnia-max-cjm-real-site.html`.
- Preserve without changes:
  - `C:\Users\79133\Downloads\omnia-max-cjm (3).html` as the structural reference;
  - `C:\Users\79133\Downloads\omnia-max-cjm-client.html` as the previous subscription-CJM iteration.
- One self-contained HTML document with embedded CSS, SVG icons, data, and JavaScript.
- No external requests, analytics, registration, payment, document acceptance, generation, or publication.
- A visible notice that all actions are a local demonstration and do not create legal or financial consequences.

## 3. Design Sources and Precedence

Visual truth comes from the current frontend implementation, not the older design-system document.

Use these sources in order:

1. `apps/web/src/app/globals.css` for colors, typography, spacing, focus, and light/graphite shells.
2. `apps/web/src/components/BrandMark.tsx` and `apps/web/src/components/ui/button.tsx` for the brand and controls.
3. `apps/web/src/app/page.tsx`, `apps/web/src/components/marketing/PublicPageShell.tsx`, and `apps/web/src/app/pricing/page.tsx` for public pages and plans.
4. `apps/web/src/components/auth/AuthCard.tsx`, `apps/web/src/app/(auth)/max/register/page.tsx`, `MaxRegisterForm.tsx`, and `MaxOnboarding.tsx` for registration and buyer verification.
5. `MaxWorkspaceShell.tsx`, `MaxProjectNav.tsx`, `MaxSectionShell.tsx`, and `MaxLivePreview.tsx` for the constructor and device preview.
6. `AccountControlCenter` and `LegalPage.tsx` for subscription management and legal surfaces.
7. `C:\Users\79133\Downloads\omnia-max-cjm (3).html` only for the CJM shell: overview map, sticky navigation, sequential traversal, scenario switching, and contextual notes.

Do not reproduce the stale charcoal/violet direction from `docs/03-design-system.md` where it conflicts with the live frontend.

## 4. Visual Language

### 4.1 Palette

- Page: `#f5f3ee`.
- Raised paper: `#fcfbf7`.
- Primary surface: `#ffffff`.
- Muted surface: `#ece8df`.
- Primary ink: `#171716`.
- Secondary ink: `#6d6962`.
- Tertiary ink: `#8d887f`.
- Hairline: `#d8d4cb`; soft divider: `#e7e3da`.
- Primary coral: `#f15a38`; hover: `#d94929`.
- Success: `#248a4b`; error: `#c63d35`; warning: `#b37a10` on a pale-yellow surface.

Coral is reserved for the current state and the single primary action. It is not used as a decorative gradient or broad background.

### 4.2 Typography

- Inter/system sans for UI and display text.
- JetBrains Mono/system monospace for 10–12 px uppercase kickers, routes, dates, plan states, and step counters.
- Hero: 52–64 px desktop, 38–44 px mobile, weight 750–800, tight negative tracking.
- Page title: 36–46 px desktop, 30–34 px mobile.
- UI: 12–15 px; legal and explanatory body: 14–16 px with comfortable line height.

### 4.3 Geometry and chrome

- 64 px brand header.
- Desktop content width: 1320 px for public pages; 1120 px for onboarding; product shell fills available width.
- Mobile gutters: 20 px; tablet/desktop gutters: 32 px.
- Controls: 8 px radius and at least 44 px height.
- Cards: 12 px radius, paper or white fill, beige border, restrained warm shadow.
- Product preview: black phone frame with a 390×844 logical screen, version/status rail, and green connection indicator.
- Separation relies on hairlines and `gap: 1px` grids, not heavy shadows.

## 5. CJM Shell

The shell retains the strongest ideas from the supplied reference while removing its technical overload.

### 5.1 Overview

- Hero in the actual Omnia marketing style: graphite panel, white value statement, coral CTA, and phone/workspace preview.
- Summary counters: 17 customer touchpoints, 7 phases, 3 plan paths, 1 contract gate, 0 hidden renewals.
- Seven phase cards and seventeen clickable touchpoint cards.
- Phase filters and a primary “Пройти весь путь” action.
- Global scenario selector for happy paths and recoverable problems.

### 5.2 Sequential view

- Sticky top bar: BrandMark, current route, current step, “Карта пути”, and “Примечания”.
- Main area renders a faithful Omnia screen, not a diagram of that screen.
- Sticky bottom navigation: Back, progress, and one next action.
- The active screen has only one visually dominant CTA.

### 5.3 Notes panel

Desktop: 380–420 px side panel. Mobile: bottom sheet/overlay that preserves the screen behind it.

Four customer-facing tabs:

1. **Что видит человек** — the user’s question and answer at this step.
2. **Почему так** — the product decision and expected confidence gain.
3. **Условия** — what must be true before continuing.
4. **Если не получилось** — the recovery path and what remains saved.

Do not expose source code, APIs, server state, internal services, LLM names, databases, or deployment details in the ordinary user layer.

## 6. Seven Phases and Seventeen Touchpoints

### Phase A — Value and entry

#### 1. MAX landing

- Actual Omnia header, graphite hero, value proposition, phone preview, and one CTA: “Создать приложение в MAX”.
- Secondary action: see a short example without registration.
- Outcome: the user understands the concrete result and expected time to first preview.

#### 2. Result preview

- Three-screen phone carousel showing a realistic generated mini-app.
- Compact explanation of what Omnia creates and what remains under the owner’s control.
- CTA: “Начать со своей задачи”.
- Outcome: value is understood before requesting account data.

### Phase B — Account and buyer

#### 3. Registration

- Faithful two-column MAX registration page from the current site.
- Email and password only; password visibility, requirements, inline validation.
- Separate acknowledgement of privacy information; no marketing consent bundled into registration.
- Outcome: a minimal account is created in the demonstration.

#### 4. Email confirmation

- Clear address, resend timer, change-email action, and recovery for an expired link.
- Confirmation explains that contract records and receipts will be sent to this address.
- Outcome: verified contact channel.

#### 5. Buyer and authority

- Three-step onboarding visual borrowed from `MaxOnboarding`.
- Choose organization, individual entrepreneur, or self-employed person.
- Request only contract-relevant fields: name, INN, representative, and separate authority confirmation when needed.
- Outcome: the contracting party is explicit and editable.

### Phase C — Task and recommendation

#### 6. Business brief

- One compact card, progressive questions, examples, and autosave indicator.
- Fields: business, audience, primary action, content/material readiness, desired launch date.
- Warn against entering passwords, payment data, or third-party personal data without a lawful basis.
- Outcome: a usable brief without professional terminology.

#### 7. Future-app summary

- Omnia returns a plain-language summary: proposed screens, customer action, source materials, integrations, and launch assumptions.
- Every block is editable before purchase.
- Outcome: the user sees what will be generated and corrects misunderstandings early.

#### 8. Plan selection

- Three actual-site pricing cards: Free, Pro, Business.
- Equal visual availability; recommendation is explained but not preselected.
- Each card shows limits, publication capability, team size, included allowance, current price, and renewal state.
- Free: `0 ₽`, no card, no auto-renewal.
- Pro: `1 490 ₽` for one month in the approved demonstration catalog.
- Business: `4 990 ₽` for one month in the approved demonstration catalog, visibly marked as a dated catalog snapshot.
- Outcome: plan choice is informed and reversible before contract acceptance.

### Phase D — Contract and activation

#### 9. Order summary

- Show buyer, selected plan, price now, period, renewal state, next possible charge, cancellation method, and receipt email.
- Edit links return to the exact prior step without losing the brief.
- Outcome: no material term is hidden behind the payment action.

#### 10. Contract gate

- Quiet legal surface based on `LegalPage` within the product shell.
- Full demo offer, privacy policy, and separate consent texts open inline.
- Separate controls:
  - required offer acceptance;
  - required personal-data consent only where consent is the chosen legal basis;
  - optional marketing consent, off by default;
  - optional paid-plan auto-renewal, off by default;
  - no renewal control for Free.
- Outcome: the parties and material terms are accepted before the first generation.

#### 11. Activation

- Free branch: “Активировать Free”, no payment method.
- Paid branch: exact amount for the current period, renewal state, receipt address, and demo payment action.
- A failed payment keeps the brief, buyer, plan, and accepted document version; the user can retry, change method, or switch to Free. Switching plans requires a fresh plan summary and contract acceptance.
- Outcome: an active plan with an understandable confirmation.

### Phase E — First creation

#### 12. Final generation brief

- MAX Workspace shell with project navigation and right-side phone preview.
- The brief is shown in a compact editable summary, with one CTA: “Начать первую генерацию”.
- Show plan and remaining included allowance without internal accounting jargon.
- Outcome: the user knowingly starts the agreed task.

#### 13. Generation progress

- Four customer-readable stages: understand the task, assemble screens, check the main scenario, prepare the preview.
- Real-time visual progress, elapsed time, safe-close message, and no hidden new consents.
- Error state includes retry, return to brief, and support; progress and plan remain saved.
- Outcome: generation feels controlled, not opaque.

#### 14. Review and corrections

- Phone preview, version rail, checklist, and a conversational correction box.
- Clearly distinguish owner-provided materials, platform components, third-party licenses, and generated output.
- Do not promise absolute uniqueness or automatic exclusive rights to fully machine-generated output.
- Outcome: the owner can review and correct before publication.

### Phase F — Publication

#### 15. MAX readiness

- Human-readable checklist: owner confirmed, plan active, content reviewed, bot connection ready, publication name chosen.
- Missing items open the exact recovery step without losing completed work.
- Outcome: publication is predictable and reversible.

#### 16. Publish and verify

- One primary action to publish the reviewed version.
- Progress states: preparing, publishing, checking the public URL, ready.
- Failure offers retry or rollback to the last ready version.
- Success shows the public MAX URL and next recommended action.
- Outcome: a verified published result, not a vague “done” badge.

### Phase G — After launch

#### 17. Project and subscription management

- `AccountControlCenter`-style screen with current plan, period end, next charge or “will not charge”, renewal state, receipt/contract history, plan change, payment-method withdrawal, cancellation/refund request, export, and support.
- Free shows no fake payment or renewal controls.
- Paid renewal and saved payment method are independent controls.
- Outcome: the owner remains in control after launch.

## 7. State and Gate Model

Use one explicit state object with these groups:

- `account`: email, password-valid, email-verified.
- `buyer`: type, name, INN, representative, authority-confirmed, confirmed snapshot.
- `brief`: answers, saved flag, summary-approved.
- `plan`: selected key, selection timestamp, catalog version.
- `contract`: offer version, offer accepted, data consent, marketing, auto-renewal, accepted buyer/plan snapshot.
- `activation`: Free active or demo payment state, period end, receipt state.
- `generation`: stage, status, saved version, failure/retry.
- `publication`: readiness items, status, public URL, last ready version.
- `management`: renewal, payment-method permission, cancellation/refund state.

Global invariants:

1. Contract acceptance requires verified email, confirmed buyer, saved brief summary, and selected plan.
2. Activation requires an accepted contract snapshot matching the current buyer, plan, and offer version.
3. Generation requires activation; visiting a later card never manufactures missing state.
4. Publication requires a ready reviewed generation.
5. Editing buyer details, authority, selected plan, or offer version invalidates only the dependent confirmations and explains what must be repeated.
6. Errors never erase unrelated completed work.

## 8. Demonstration Scenarios

The selector must support at least:

1. Fresh visitor.
2. Happy path — Free.
3. Happy path — Pro without renewal.
4. Happy path — Business with explicit renewal.
5. Email link expired.
6. Buyer details invalid.
7. Authority not confirmed.
8. Business/INN already linked to another owner.
9. Offer updated before acceptance.
10. Required consent missing.
11. Payment failed.
12. Payment failed → switch to Free with fresh acceptance.
13. Brief incomplete.
14. Generation interrupted.
15. Result requires correction.
16. Publication prerequisite missing.
17. Publication failed with retry/rollback.
18. Renewal disabled and payment permission withdrawn.

## 9. Interaction and Persistence

- Overview, direct touchpoint selection, Back/Next, phase filter, and reset.
- Buttons and fields update a shared state machine and re-render only the prototype view.
- Preserve demo progress in `localStorage` under a versioned key; provide a visible reset.
- Directly opened protected steps render an honest gate and route to the first missing prerequisite.
- Focus is restored after render to the requested enabled control, otherwise to the screen title.
- Notes close on step change and reopen on demand.

## 10. Responsive and Accessibility Requirements

- Verify at 1440×900, 1024×768, 768×1024, and 390×844.
- No horizontal overflow at 390 px or at 200% text scaling.
- Below desktop, navigation collapses and phone preview becomes a drawer/sheet.
- Notes become a bottom sheet without destroying screen context.
- Visible focus, logical Tab order, programmatic labels, semantic headings, accessible names, and `aria-live` for status changes.
- Color is never the only indicator; selected/current/error/success states include text and shape/icon differences.
- Respect `prefers-reduced-motion`.
- All touch targets are at least 44×44 px.

## 11. Verification

### Structural checks

- Exactly 17 touchpoints and 7 phases.
- All three plans and all 18 scenarios are present.
- No external `src`, `href`, `fetch`, XHR, WebSocket, analytics, or form submission.
- Original reference and previous CJM hashes remain unchanged.

### Browser checks

- Complete Free, Pro-no-renewal, and Business-renewal paths.
- Contract gate blocks every branch before generation.
- Free never requests a card or shows renewal management.
- Marketing and renewal begin unchecked and do not block the main contract.
- Buyer/plan/offer edits invalidate dependent acceptance and keep a visible explanation through multi-character input.
- Payment, generation, and publication errors recover without losing the brief.
- Direct late-step navigation shows the correct gate.
- Keyboard, focus restoration, responsive layouts, light/dark graphite surfaces, and 200% text.
- Zero external requests and zero browser exceptions.

### Independent review

Review the finished artifact against this specification and the named production components. Treat missing gates, invented legal facts, fake Free payment controls, dishonest late-step states, inaccessible interactions, or visual drift from the live Omnia system as blocking findings.

## 12. Legal and Product Boundaries

- This artifact is a UX demonstration, not an offer, contract, payment form, production registration, or generation service.
- It may show the existing demo seller status, INN, and support contact only where already verified; it must not invent missing legal name or address.
- Before production use, an authorized Russian lawyer must approve seller details, personal-data roles and processors, receipt/refund language, subscription catalog, and final consent wording.
- The Business price remains a dated demonstration assumption until the owner confirms the production catalog.

## 13. Non-Goals

- Do not change the production frontend in this iteration.
- Do not implement real authentication, payments, generation, bot connection, publication, emails, receipts, or analytics.
- Do not add backend, API, database, deployment, or external-library dependencies to the standalone artifact.
- Do not reproduce the current bad journey merely because a route already exists; production components are visual building blocks, while this specification defines the improved sequence.
