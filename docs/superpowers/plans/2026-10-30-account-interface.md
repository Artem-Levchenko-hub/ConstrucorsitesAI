# Account Interface Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for remaining inline work and one final independent review. Support/billing slices were already assigned with disjoint ownership.

**Goal:** Make support and billing compact, aligned and pleasant to navigate.

**Architecture:** Scoped support/billing styles extend the existing account shell. One stable client transition surface enhances account pages and admin tab content without replacing children.

**Tech Stack:** React 19, Next.js 15, shared easing token, live matchMedia subscription, CSS and Web Animations API. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-30-account-interface.md`

## Global Constraints

- Controls: 44px target. Panel corners: 16px. Responsive natural heights.
- Preserve pricing, quota arithmetic, API requests, permissions and business data.
- No keyed child remounts, outgoing-content retention or builder/global motion.
- Reduced motion disables entrance movement; unsupported animation APIs remain usable.
- Commit, push, exact-revision CI, canonical web-only deployment and health evidence.

## Review Focus

- Long Russian subjects, emails and package allowances wrap without truncation.
- Draft values, React state and keyboard focus survive transition-trigger changes.
- Permission revocation removes private content immediately, including mid-animation.
- Reduced motion and missing Web Animations API preserve usable content.
- Payment idempotency, support deep links, archive navigation and query semantics remain intact.

## Tasks

- [x] Refine support controls/cards in AdminSupportPanel and scoped CSS; run existing support/admin behavior tests.
- [x] Refine AccountBilling, balance and usage CSS; retain all copy/data flow and run existing billing suites.
- [ ] Add motion regressions, observe a failing transition assertion, implement stable account transition surface and integrate AccountShell/admin tabs.
- [ ] Compact shared account spacing and unify control/panel tokens; preserve checkbox/radio sizing.
- [ ] Review combined diff; run focused/full web tests, full typecheck/lint and CSS syntax checks.
- [ ] Commit/push, verify exact main CI, deploy through documented web-only path, confirm fresh health and retained orders; update report with actual evidence and remaining browser limitation.

## Verification ledger

- Initial transition assertions: 3 failed / 1 passed against a pass-through surface; implementation then passed 131 focused checks.
- Independent review identified Framer Motion's mount-only preference snapshot. A real media-event regression failed (1 failed / 4 passed); replace it with useSyncExternalStore rather than mock the hook.
- Ruling: use two local Vitest workers. Concurrent full and focused runs produced timeouts and an UNKNOWN dependency lstat error on Windows; stopped only the verified owned test process tree. Exact-revision Linux CI remains the release gate.
