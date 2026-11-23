# Mobile builder implementation plan

Goal: a comfortable installable mobile owner workspace using existing Next.js and React components.
Spec: ../specs/2026-11-23-mobile-builder-design.md

- [x] Add and verify regression tests for mobile tab destinations, preview action, manifest, and install dismissal / platform fallback.
- [x] Add shared MaxMobileNav and integrate editor and section shells. Pass explicit preview entry from server route, preserving existing legacy redirects.
- [x] Add manifest, brand PNG icons, Apple standalone metadata and an optional accessible install dialog. Reuse it in owner menus.
- [x] Consolidate mobile styles for bottom navigation, compact headers, composer, grouped menu, preview and management controls. Preserve desktop rules.
- [x] Run relevant tests, TypeScript, lint and diff checks. Verify local UI at 320×640, 393×852, and desktop 1366×900.
- [x] Show the local version for review. The owner requested production rollout after the mobile catalog and preview were verified.

Release the pushed revision after its CI passes, using the documented web-only deployment. Verify the live catalog, preview, and service health.
