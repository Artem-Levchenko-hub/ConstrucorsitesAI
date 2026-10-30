# Real guide implementation plan

> **For agentic workers:** use bounded parallel execution in this existing isolated worktree; root alone owns browser capture, Git and deployment.

**Goal:** Replace synthetic guide screens with current genuine Yleum and MAX screenshots and clear Russian instructions.

**Architecture:** A curated screenshot manifest stores provenance, dimensions and proportional callouts. A shared read-only screenshot component renders images, arrows, captions and enlargement links. The guide retains existing anchors and uses these captures throughout the owner journey.

**Tech Stack:** Next.js, React, TypeScript, CSS, existing Vitest; Browser in-app capture.

**Spec:** `docs/superpowers/specs/2026-10-30-real-guide-design.md`.

## Global constraints

- Use only genuine screenshots; no invented MAX UI, status or controls.
- Never publish tokens, OTPs, private email, conversations or business customer data.
- Browser handles remain with root. No paid AI generation, SMS or business data mutation for documentation.
- Preserve `/max/guide` anchors; no customer version history or rollback.
- All subagents: gpt-6.1-sol, max. Git/deployment are serial and root-owned.

## Review focus

- Captions and arrows match the actually captured buttons.
- Responsive scaling keeps arrows on their target and text readable.
- Direct image links expose only curated public assets.
- Phone verification, three publication prerequisites and four-step MAX connection are distinguished.
- Free catalog saving never implies a paid AI change or a new MAX publication.

## Tasks

- [x] Root: capture and curate 19 real owner/MAX screens; write `guide-screenshots.ts` with provenance, image dimensions and callouts; visually inspect every public asset. Registration is shown by its genuine post-login result; no fabricated SMS form. Raw MAX client screenshots remain private and are not published.
- [x] Screenshot implementer: replace `GuideVisuals.tsx` with reusable image + SVG overlay renderer, focused CSS and meaningful guide-surface tests; no fake UI controls. Integrated focused run: 9 tests pass, including all 15 chapters and all 19 real captures.
- [x] Content implementer: rewrite `app/max/guide/page.tsx` against real current controls and root-provided capture IDs; retain anchors and simplify Russian copy. Align the MAX connection helper with the observed MAX Business path.
- [ ] Root + independent reviewer: inspect assets, captions, mobile/desktop rendering and tests; fix relevant issues.
- [ ] Root: focused tests, typecheck/lint, commit/push, exact CI, canonical web-only deployment, fresh health and live guide image checks. Report revision and remaining factual limits.
