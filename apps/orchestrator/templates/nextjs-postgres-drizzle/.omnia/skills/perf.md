# Skill: perf — hit the performance budget

Read before pages with images, fonts, data lists, or above-the-fold content. The
**perf-a11y gate measures the live render and BLOCKS** when it misses the budget:
TTFB < 800ms, **LCP < 2.5s**, **CLS < 0.1**, Lighthouse-equivalent **perf ≥ 85**
(mobile, 390px). These are mostly won by construction — do the few things below.

## Images (the usual LCP + CLS killer)
- Use `next/image` `<Image>`, never a raw `<img>`. ALWAYS pass `width`+`height`
  (or `fill` with a sized parent) — a sized image reserves space, so it can't
  cause layout shift (CLS). Add `priority` to the ONE above-the-fold hero image so
  it paints fast (LCP); lazy-load the rest (the default).
- Don't ship huge source images into a hero `background-image`; prefer `<Image>`.

## Fonts (silent CLS + paint delay)
- Use `next/font` (already wired in the layout) — it self-hosts and reserves
  metrics so text doesn't reflow. Don't add `<link>` to Google Fonts by hand.

## Layout stability (CLS)
- Reserve space for anything async: skeletons for lists (`@/components/omnia`
  gives loading states), fixed dimensions for media/embeds. Never let content pop
  in and push the page down.

## Above-the-fold + JS (LCP, TBT, perf score)
- Keep the hero light: render it server-side, no heavy client component just to
  show text. Mark only the interactive leaves `"use client"`, not whole pages.
- Don't pull a big chart/animation library for the first screen. Lazy-load
  (`next/dynamic`) anything below the fold or interaction-gated.
- Data lists: page/limit via the SDK (`entities.X.list({ limit, page })`) — never
  fetch thousands of rows to render ten.

## Don't fight the gate
- Animations are fine but use `transform`/`opacity` (compositor-cheap), not
  layout-triggering properties; respect `prefers-reduced-motion`.

Self-check before `done`: hero uses `<Image priority width height>`; lists are
paginated + have skeletons; no hand-added font `<link>`; below-the-fold heavy UI
is `next/dynamic`.
