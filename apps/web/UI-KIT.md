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

`src/components/ui-kit/motion-preview.ts` and `MotionLab.tsx` are imported only by
the catalog. Do not connect these proposals to product screens before approval.

| Interaction | Timing | Movement |
| --- | --- | --- |
| Press | 140 ms | scale 0.985 |
| Switch | 140 ms | thumb travels 20 px |
| Section selection | 180 ms | shared selection indicator |
| Disclosure | 180 ms | local grid expansion |
| Panel | 240 ms in / 120 ms out | 4 px, scale 0.99 |
| Confirmation | 180 ms | opacity crossfade |

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

Accepted locally: production build (including typecheck); full existing frontend
suite, 171 files / 1927 tests; added live-preference regression, 1 test; full lint
with no errors (existing `apple-icon.tsx` image warning); clean diff whitespace.
Chromium acceptance passed at 1440, 390 and 320 px in both themes: dialogs and
focus return, portal theme, menu, local form, loading/result, all six motion
examples, manual and live OS reduced motion, no overflow, runtime exceptions or
mutating requests. Screenshots and JSON: `.release-evidence/ui-kit-20261110/`.

Local cleanup: shared catalog sections and variant tables, reusable demo layout,
central preview settings, one live media-query subscription with cleanup. No
production components or existing motion presets were changed.

Delivery remains blocked. Base local/upstream/public web and API revision was
`92961fa06b6f92a9eeb14d5a3fbd2f830bd1872d`. The documented `max-core` SSH alias
is absent; `zeuszcz@2.153.248.98` times out. Current `yleum.ru` resolves to
`2.153.248.133`, but `zeuszcz@yleum.ru` rejects the available keys with
`Permission denied (publickey)`. Both public health endpoints return `status=ok`
for the base revision; this does not confirm delivery of this catalog.

Changed scope: `src/app/ui-kit/page.tsx`, `src/components/ui-kit/*`, this document,
and progress record H296 / V1 in `../../otchet/data.json`. Next: restore the
documented production SSH configuration/access and required `gh` CLI, confirm
green CI for the pushed catalog revision, run
`infra/release/deploy-prod.sh <full-pushed-sha> --web-only`, then confirm web
container status, `/web-health` matching that SHA, `/ui-kit` HTTP 200 and API
health. Keep the examples unlinked from product flows.
