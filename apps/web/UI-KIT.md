# Yleum UI kit

Open `/ui-kit`. The catalog is a standalone, unlinked, noindex route. It does not
create projects, invoke models, publish applications, or save business data.

## Existing system

- `src/app/globals.css`: dark theme, semantic colors, radius and type tokens.
- `src/components/max/max-studio.css`: opt-in light MAX theme (`data-max-studio`).
- `src/components/ui/*`: actual Button, Input, Textarea, Label, Badge, Avatar,
  Skeleton, Dialog and DropdownMenu implementations imported by the catalog.
- `src/app/fonts.css`: local Inter, Onest and JetBrains Mono.
- `src/lib/motion.ts`: existing production motion language; unchanged.

Palette values are read from computed theme variables. Portal roots receive the
selected theme as well. Existing component appearances and behavior are preserved;
the catalog documents them rather than replacing them with copies.

## Proposed motion, for review only

`src/components/ui-kit/motion-preview.ts`, `MotionLab.tsx` and its example groups
are imported only by the catalog. Do not connect these proposals to product
screens before approval. The lab contains 14 examples in three groups, with
optional half-speed playback. Slow playback never overrides reduced motion.

| Interaction | Timing | Movement |
| --- | --- | --- |
| Press | 180 ms | scale 0.97 |
| Switch | 180 ms | thumb travels 20 px |
| Section selection | 260 ms | shared selection indicator |
| Icon change | 180 ms per phase | 45° rotation and crossfade |
| Checkbox | 260 ms | check path and slight scale |
| Tooltip | 180 ms | 6 px, opacity; hover, focus, touch, Escape |
| Disclosure | 260 ms | local grid expansion |
| Panel | 340 ms in / 160 ms out | 10 px, scale 0.96 |
| List | 260 ms | insertion and layout reorder |
| Step navigation | 340 ms per phase | directional 24 px slide |
| Number change | 260 ms | vertical 14 px replacement |
| Confirmation | 180 ms per phase | 6 px text replacement |
| Notification | 340 ms in / 160 ms out | 10 px, scale 0.96 |
| Progress | 400 ms | scaleX fill to actual value |

The lab reuses the existing `EASE_OUT` curve `[0.16, 1, 0.3, 1]`. No bounce,
autoplay, scroll choreography, hover lift, persistent timers, or new dependencies.
Repeated actions can interrupt transitions. OS reduced motion takes precedence
over the lab checkbox: spatial changes become immediate and meaningful opacity
feedback takes 80 ms. The skeleton animates only after explicitly requesting its
example and stays static with reduced motion.

Keep durations in the preview settings and selectors under `.uk`. Before any
future rollout, approve the examples and check the actual target flow, focus,
interruption, mobile layout and reduced motion separately.

## Checkpoint — 2026-11-10

Current iteration accepted locally: build including typecheck; full lint with no
errors (the existing `apple-icon.tsx` image warning remains); three regression
tests for live reduced motion, preserving demo state and keyboard tooltip
dismissal. Chromium acceptance passed for all 14 examples, both themes, 1440,
390 and 320 px, slow playback and live OS preference changes. No overflow,
runtime errors or mutating requests. Current evidence:
`.release-evidence/ui-kit-expanded-20261110/`.

Initial catalog acceptance: production build (including typecheck); full existing frontend
suite, 171 files / 1927 tests; added live-preference regression, 1 test; full lint
with no errors (existing `apple-icon.tsx` image warning); clean diff whitespace.
Chromium acceptance passed at 1440, 390 and 320 px in both themes: dialogs and
focus return, portal theme, menu, local form, loading/result, all six motion
examples, manual and live OS reduced motion, no overflow, runtime exceptions or
mutating requests. Screenshots and JSON: `.release-evidence/ui-kit-20261110/`.

Local cleanup: shared catalog sections and variant tables, reusable demo layout,
central preview settings and transition helpers, a scoped preview context,
separate example components with independent state, one live media-query
subscription with cleanup. CSS durations and displayed timings derive from the
same preview settings. No production components or existing motion presets changed.

Production delivery is explicitly deferred by the owner: “до прода пока ниче
не надо доводить”. Continue local review only; do not attempt deployment or
connect the proposals to product screens. The original catalog was committed
and pushed as `9ea73667c69d5bd28259e24c4712125019a71831`; production deployment
was not performed. The earlier SSH access problem does not require action while
the owner requests local-only work.

Changed scope: `src/app/ui-kit/page.tsx`, `src/components/ui-kit/*`, this document,
and progress record H296 / V1 in `../../otchet/data.json`. Current iteration:
6 → 14 examples, clearer motion and half-speed review. Next: review examples
locally at `/ui-kit#motion`, then refine selected transitions before any rollout.
