# Admin and project clarity implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for the admin task; independent menu, project-list and wizard tasks are assigned through `dispatching-parallel-agents`.

**Goal:** Deliver the approved light admin center and the user's menu, project identity/status and questionnaire hierarchy corrections to production.

**Architecture:** Keep the existing React Query/API contracts and authorization guards. Use scoped presentation components and semantic tables; real server data remains the only source for state and images.

**Tech Stack:** Next.js 15, React 19, Radix, scoped CSS, Vitest/jsdom.

**Spec:** User-approved design: light admin theme, compact accounts table, separate status column, actions behind «Действия», matching organizations and audit. Additional requests: stable account-menu hover, colored project states, actual application images when available, stronger questionnaire summary hierarchy.

## Global constraints

- No backend, permission, payment, user-role or real-account mutations during QA.
- Preserve self-administration restrictions and existing mutation payloads.
- No generic gray placeholder screenshots, new dependencies or global theme reset.
- Readable labels/icons supplement color; keyboard navigation and 320px layout remain usable.
- Tests before implementation; complete repository commit/push/web-only production delivery.

### 1. Admin center (controller)

Files: `apps/web/src/components/account/{AccountShell,AdminControlCenter,AdminUsersPanel,AdminVerificationPanel,AdminAuditPanel}.tsx`, new `admin.css` and shared presentation helpers; tests `max-style-boundaries.test.tsx` and new `admin-control-center.test.tsx`.

- [x] Render existing shell in a regression test and require the same light boundary as account pages, an active admin navigation item and readable foreground contrast.
- [x] Mount actual panels against fixture HTTP responses. Require semantic tables, search filtering, action-menu self guards, no mutation on menu opening, exact PATCH/decision payloads on selection, keyboard tabs and retryable failures.
- [x] Run `pnpm exec vitest run src/lib/__tests__/admin-control-center.test.tsx src/lib/__tests__/max-style-boundaries.test.tsx`; observe failures before implementation.
- [x] Remove the admin-only dark override; add compact tab navigation, table rows, explicit status labels and scoped light action menus. Organization review expands locally before the existing decision controls.
- [x] Rerun targeted tests and inspect all three tabs at desktop/mobile widths against read-only fixtures.

### 2. Account menu (assigned: fix_account_hover)

Files: `MaxStudioHeader.tsx`, new scoped dropdown CSS and regression test. Keep Radix semantics; distinguish pointer highlighting from keyboard focus without changing row geometry. Validate pointer/keyboard, Escape and reduced motion.

### 3. Project identity and status (assigned: project_identity_status)

Files: `MaxStudioProjectCard.tsx`, `max-studio.css`, focused helper/test. Preserve readiness computation and route actions. Use available project image data with safe source validation and broken-image fallback. No per-row preview iframes or invented progress.

### 4. Wizard review (assigned: wizard_review_hierarchy)

Files: `MaxProjectWizard.tsx`, new scoped review CSS/component and test. Group name/idea, emphasize main action, list functions, compact optional metadata; keep all values and creation payload intact.

### 5. Integration and delivery (controller)

- [x] Review combined diff and targeted agent evidence. Independent reviewer did not complete; controller performed the combined review and browser checks.
- [x] Run full Vitest suite (376/376 across 61 files after translating two historical audit actions), typecheck, lint, production build (46/46 pages) and `git diff --check`.
- [x] Update `otchet/data.json` with verified evidence and an unscored testing hypothesis; validate unrelated steps remain unchanged.
- [x] Commit and push without force. Build the exact web image; initial web-only rollout 303053814d41a82bc7afd28c959d1da35c78255f healthy, backend IDs and start times unchanged. Rollback baseline was a72b40889d88b5a7f7538bd3b6dd91d231a192ba.
- [x] Deliver the audit-label follow-up ff2ecfbce2fdff2eb36e8009ff15e826310ff3e0, using the verified 303053814d41a82bc7afd28c959d1da35c78255f image as rollback. Release record: `/tmp/omnia-admin-clarity-release.hgIAiK`.
- [x] Verify exact public web-health revision ff2ecfbce2fdff2eb36e8009ff15e826310ff3e0, healthy web and unchanged non-web IDs/start times. Read-only production journal shows the translated historical actions and preserved role changes/notes. Report version 71 records delivery; user impact remains unmeasured, missing project images remain honestly documented.
