# Phase 4 — Awwwards-grade design in generated apps (grounded punch-list)

> **Status:** SPEC (Phase 4.1 first sub-slice). Implementation + visual E2E = follow-on ticks.
> **Author:** continuous-quality routine, 2026-06-10.
> **Scope of this doc:** turn the vague goal "apps must look awwwards-level" into a concrete,
> file-by-file change list, grounded in the *current* nextjs-entities app-kit source and the
> *landing* design language we already ship. Docs-only — no runtime touched by this file.

## 0. Where we are (measured, not guessed)

The generated-app kit (`apps/orchestrator/templates/nextjs-entities/src/components/omnia/`) is
**already enterprise-clean** — not the problem people think it is:

- **Tokens** (`src/app/globals.css`): full OKLch palette, light+dark, `--chart-1..5`, elevation
  scale (`--elevation-1/2`), radius scale, Manrope + JetBrains Mono. WCAG-safe contrast.
- **Components:** `AppShell` (responsive sidebar/sheet/topbar), `CrudResource`, `DataTable`
  (search/sort/paginate + skeleton + empty), `EntityForm` (schema-driven), `StatCard`
  (`hover-lift`, tabular-nums, trend pill), `EmptyState` (dashed box + icon), `PageHeader`.
- **a11y/motion:** `focus-visible:ring-[3px]` on Button/Input, `aria-current` on nav,
  `prefers-reduced-motion` gate on all animations, `.stagger` entrance.
- **Art-director brief:** `apps/api/src/omnia_api/services/prompt_builder.py` `_ENTITIES_UI`
  (L823–948) mandates kit-from-parts, token-only colors, responsive grids, fixed globals.css.

**The gap is the last 20%:** the kit is *competent and flat*. It targets "Linear/Notion/Vercel
clean" but reads generic — it has none of the **living-layer / type-as-graphic / depth-not-flat**
moves that make our *landings* feel premium (memory: `omnia_design_v4_living_leap`,
`omnia_graphic_arsenal_v5`, `omnia_hero_accent_typography`). And **charts are absent** (4.2).

Awwwards-grade ≠ more chrome. It means: one confident accent, deliberate depth, type that does
graphic work, and motion that rewards — applied with restraint inside a working app.

---

## 1. Phase 4.1 — port the landing design language into the app-kit

Each item: **target file → concrete change → acceptance criterion**. All changes live in the
nextjs-entities template (kit components + globals.css tokens + `_ENTITIES_UI` brief). Remember:
template source changes only reach new apps after `scripts/build-template-images.sh` rebuilds
`omnia-template-nextjs-entities:dev` (memory: `omnia_crm_demo_and_template_image`).

### 1.1 Depth, not flat — surface elevation system
- **File:** `globals.css` + `stat-card.tsx` + `app-shell.tsx`.
- **Change:** introduce a 3-tier surface model used consistently — `bg-background` (canvas),
  `bg-card` resting on `--elevation-1`, raised/interactive on `--elevation-2`. Give the dashboard
  a subtle layered backdrop: a single `radial-gradient` glow token (`--app-glow`, accent at ~6%
  alpha) behind the page header band — the app analog of the landing's living mesh, but quiet.
  StatCards get a hairline top accent (`border-t-2 border-primary/15`) only on the primary KPI.
- **Acceptance:** dashboard reads as layered planes (canvas → card → raised) not one gray sheet;
  exactly one accent glow per screen; no card looks like a flat outlined box.

### 1.2 Type-as-graphic in app headers (restrained)
- **File:** `page-header.tsx` + `_ENTITIES_UI` brief.
- **Change:** the public landing of each app (`src/app/page.tsx`) inherits the hero doctrine
  (`_HERO_GRAPHIC`, memory `omnia_hero_accent_typography`) — ONE hero mode: type-as-graphic
  (`text-stroke`/`text-blend` capture-safe primitives) OR darkened photo-art. Inside the cabinet,
  `PageHeader` title goes `text-2xl` → `text-2xl sm:text-3xl font-semibold tracking-tight` with an
  optional eyebrow (`text-xs font-medium uppercase tracking-wider text-muted-foreground`) so the
  page title has hierarchy weight, not just size. NOT decorative — title still scannable.
- **Acceptance:** app landing hero uses exactly one deliberate hero mode (no generic centered
  text on flat bg); cabinet page headers have eyebrow + tight display title.

### 1.3 Living-layer micro-motion (reward, reduced-motion safe)
- **File:** `globals.css` (utilities) + `stat-card.tsx` + `crud-resource.tsx`.
- **Change:** add three capture-safe, `prefers-reduced-motion`-gated motions:
  (a) **count-up** on StatCard numeric values (CSS `@property --num` tween or tiny JS, ≤500ms);
  (b) **stagger-in** already exists — apply `.stagger` to the dashboard StatCard grid + first
  table render; (c) **trend-pill rise** (fade+translateY 4px) when a KPI mounts. All ease-out
  ≤300ms. No infinite/looping motion (distracting in a work tool).
- **Acceptance:** dashboard mount feels alive (numbers settle, cards cascade) once; with
  `prefers-reduced-motion: reduce` everything is static; no layout shift.

### 1.4 Palette discipline — one accent, muted support
- **File:** `_ENTITIES_UI` brief + `globals.css`.
- **Change:** enforce the landing doctrine (memory `omnia_design_v4_living_leap`: "доминанта +
  приглушённый акцент"). Brief already allows `--primary` override; add: secondary actions use
  `variant="outline"`/`ghost` (never a second saturated color); semantic colors (success/warning/
  destructive) only on data, never as decoration. One `--primary` per app, picked from the
  niche's mood, applied to CTA + active nav + focus ring only.
- **Acceptance:** any generated app screen has exactly one dominant accent; no rainbow buttons.

---

## 2. Phase 4.2 — charts, empty-state illustrations, micro-motion

### 2.1 Vanilla chart primitives (NO dependency — matches kit-v5 "Aceternity→vanilla")
- **File (new):** `src/components/omnia/charts.tsx`.
- **Change:** ship dependency-free SVG/CSS chart primitives so dashboards stop being KPI-tiles +
  table only (charts currently ABSENT; prompt forbids recharts/chart.js — keep it that way):
  - `<Sparkline data={number[]} />` — inline trend line for StatCard.
  - `<BarMini data={{label,value}[]} />` — horizontal bars, `--chart-1..5` tokens.
  - `<DonutStat value pct />` — single-metric ring (CSS conic-gradient).
  - `<TrendArea>` — small filled area for a primary dashboard metric.
  All use `--chart-*` tokens, `currentColor`, `aria-label` + visually-hidden data table fallback.
- **Acceptance:** a dashboard can render a real trend/breakdown chart with zero new packages;
  charts respect tokens + dark mode; screen-reader gets the numbers.
- **Brief:** add a `_ENTITIES_UI` line — "дашборд с динамикой → используй `Sparkline`/`BarMini`/
  `DonutStat` из кита (НЕ ставь chart-либы)".

### 2.2 Empty-state illustrations (themed, not generic box)
- **File:** `empty-state.tsx`.
- **Change:** replace the single Inbox-icon-in-circle with a small inline SVG illustration slot
  (themed vector, the app analog of the landing's mandatory thematic vector drawings —
  `omnia_graphic_arsenal_v5`). Provide 3–4 built-in line-art SVGs (empty-list, no-results,
  first-record, error) using `currentColor` + accent, plus an `illustration` prop override.
- **Acceptance:** an empty CRUD list shows a purposeful illustration + clear primary action
  ("Создать первую запись"), not a gray dashed rectangle.

### 2.3 Micro-motion polish
- Covered by 1.3; additionally: table row hover (`hover:bg-muted/40` — confirm present),
  dialog open/close already shadcn-animated. Acceptance: interactions have ≤200ms feedback.

---

## 3. Phase 4.3 — responsive + a11y on entity + fullstack

Mostly already satisfied; close the named gaps:
- **Touch targets:** icon-only buttons (nav toggle, table sort, row actions) must be ≥44×44px
  hit area (`size-11` or padded). **File:** `data-table.tsx` sort buttons, `app-shell.tsx` menu.
- **Sort-button a11y:** DataTable sort `<button>` has text but add `aria-label="Сортировать по
  {header}"` + `aria-sort` on `<th>`. **File:** `data-table.tsx`.
- **Chart a11y:** enforced in 2.1 (label + table fallback).
- **Contrast in dark mode:** verify trend-pill `text-success`/`text-destructive` on `bg-card`
  dark ≥ 4.5:1 (OKLch values L≈0.7 — check, bump if short).
- **Acceptance:** axe/lighthouse a11y pass on a generated dashboard (desktop+mobile); keyboard
  reaches every action with visible focus; touch targets pass.

---

## 4. Phase 4.4 — apply `frontend-design` + `ui-ux-pro-max`
Use both skills when implementing §1–§3: `ui-ux-pro-max` for palette/font-pair/spacing per niche,
`frontend-design` to avoid generic AI aesthetics. Stack target: Next.js 15 + Tailwind v4 + shadcn.

## 5. Phase 4.5 — E2E on 3 verticals (acceptance gate for Phase 4)
Build one app per vertical **(store / CRM / clinic)**, screenshot desktop + mobile, judge against:
- one confident accent + deliberate depth (not flat), type-as-graphic hero,
- real chart on the dashboard, illustrated empty state,
- responsive (no h-scroll mobile), a11y pass, reduced-motion safe.
Reuse existing stands where possible (resource-guard: max one fresh build per tick). Mark Phase 4
done only when all three read enterprise/awwwards on both breakpoints.

---

## 6. Sequencing (so no tick leaves prod half-built)
1. **4.1 tokens+depth+motion** (globals.css + stat-card + page-header) — self-contained kit edit.
2. **4.2 charts.tsx + empty-state illustrations** — new file + one component, additive.
3. **4.1/4.2 brief lines** in `_ENTITIES_UI` — prompt-only, instant.
4. After each kit edit batch: `scripts/build-template-images.sh` → generate ONE test app →
   screenshot → verify → then push. Never rebuild the template image and walk away mid-build.
5. **4.3 a11y/touch** pass on the same test app.
6. **4.5 gate** last, on three verticals.

> Risk note: every §1–§3 change is **template source**; it is non-breaking to existing live apps
> (they run the already-built image) until the image is rebuilt. Rebuild + generate + screenshot
> is one atomic unit of work per tick — budget ~15–20 min, never start it inside a closing window.
