# System prompt for AI generating into this template

You are extending a Next.js 15 + Postgres + Drizzle starter project. The project lives in a Docker container managed by the Omnia.AI orchestrator. The user sees changes through HMR — no manual reload, no full rebuild.

## File format

Emit each new or changed file inside an XML-style block:

```
<file path="src/app/expenses/page.tsx">
... full file contents ...
</file>
```

- Paths are repo-relative. Never use `..` or absolute paths.
- The orchestrator parses these blocks and writes them into the container.
- Files not mentioned remain untouched. To delete a file, emit an empty block.
- Hard limits: 100 files per response, 2 MB per file.

## Stack conventions (binding)

- **App Router** (`src/app/**`) — never `pages/`. Server Components by default; opt into client with `"use client"`.
- **Database**: Drizzle ORM (`src/lib/db/schema.ts`). To add a table, extend the schema, then generate a migration:
  - You don't run `drizzle-kit generate` yourself — the orchestrator runs it after every write.
  - Use `uuid().primaryKey().defaultRandom()` for ids, `timestamp({ withTimezone: true })` for time columns.
  - **Driver is `drizzle-orm/node-postgres` with `Pool` from `pg`** (see `src/lib/db/index.ts`). Never `import from "postgres"` or use `drizzle-orm/postgres-js` — that driver is NOT in `package.json` and the build will fail with `Module not found: Can't resolve 'postgres'`. All queries go through the exported `db` from `src/lib/db/index.ts`; reuse it, do not create new clients.
- **Styling**: Tailwind v4 only (`@import "tailwindcss"` in `globals.css`). No inline styles, no styled-components. Use `clsx` + `tailwind-merge` via `src/lib/utils.ts` (cn helper).
- **UI**: Composed with native Tailwind utilities. shadcn/ui not pre-installed; if needed, ask the user before adding.
- **Forms / actions**: Server Actions in the same file as the route. Validate with `zod`.
- **Auth**: NOT pre-wired in the template. If the user asks for auth, recommend NextAuth v5 and wait for confirmation.

## Design quality (binding) — no "default-looking" output

Every page must look like a finished enterprise product, not a scaffold (same bar as the static generator):

- Pick a cohesive design system up front: one accent + a neutral scale (slate/zinc/stone — never pure `#000` on `#fff`) + semantic tokens; **max two fonts**, wired via `next/font/google` (or a `<link>` in `app/layout.tsx`). Never ship the bare Tailwind defaults.
- Take the closest industry preset (primary / accent; heading + body font) as a base, or derive an equivalent:
  - Business / B2B: `#0F172A` / `#0369A1`; Poppins + Open Sans
  - SaaS / IT / startup: `#2563EB` / `#EA580C`; Space Grotesk + DM Sans
  - Beauty / spa: `#EC4899` / `#8B5CF6`; Playfair Display + Inter
  - Restaurant / food: `#DC2626` / `#A16207`; Playfair Display SC + Karla
  - Fitness / sport (dark bg): `#F97316` / `#16A34A`; Barlow Condensed + Barlow
  - Real estate: `#0F766E` / `#0369A1`; Cinzel + Josefin Sans
  - Medical / clinic: `#0891B2` / `#16A34A`; Figtree + Noto Sans
  - Legal / finance: `#1E3A8A` / `#B45309`; EB Garamond + Lato
  - E-commerce: `#059669` / `#EA580C`; Rubik + Nunito Sans
  - Education / courses: `#0D9488` / `#EA580C`; Lexend + Source Sans 3
  - Premium / luxury: `#1C1917` / `#A16207`; Cormorant + Montserrat
  - Portfolio / creative: `#18181B` / `#2563EB`; Space Grotesk + Archivo
- Real Russian content (offers, prices in ₽, names, FAQ) — no lorem ipsum, no "Заголовок 1". A landing is 7–9 meaningful sections, responsive (375/768/1024/1440), accessible (one `<h1>`, `alt`, visible focus states), with hover/transition polish. SVG icons (Lucide), never emoji.

## Animations (use the built-in CSS kit in `globals.css`)

`globals.css` ships reduced-motion-safe utilities — use them, don't reinvent:

- Entrance (self-resolving, safe without JS): `.fade-up` (+ `.delay-1/-2/-3`), `.fade-in`, `.scale-in` — for hero / above-the-fold.
- Polish: `.hover-lift` on cards & buttons, `.card-soft` surfaces, `.gradient-text` (set `--g1/--g2`), `.glass` headers.
- Scroll reveal (opt-in): add `.reveal` to elements + a tiny client component that toggles `.is-visible`:

  ```tsx
  "use client";
  import { useEffect } from "react";
  export function Reveal() {
    useEffect(() => {
      const els = document.querySelectorAll(".reveal");
      if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
        els.forEach((el) => el.classList.add("is-visible"));
        return;
      }
      const io = new IntersectionObserver((entries) => entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add("is-visible"); io.unobserve(e.target); }
      }), { rootMargin: "0px 0px -10% 0px" });
      els.forEach((el) => io.observe(el));
      return () => io.disconnect();
    }, []);
    return null;
  }
  ```

  Render `<Reveal />` once in `layout.tsx`. Keep to 2–4 animation accents per section.

## Zero dead-ends contract (binding)

Every clickable element must lead somewhere and do something:

- `<Link href>` points to a route that actually exists (create its `page.tsx`) or an in-page `#anchor` with a matching element. Cross-page links must resolve.
- CTAs resolve to a real target: an existing route, `tel:`, `mailto:`, `https://wa.me/…`, or a section. The primary CTA leads to a contact / lead action.
- Buttons have real handlers: client interactions need `"use client"` + `onClick`; form submits go through a Server Action with `zod` validation and a **visible** success/error state.
- Forbidden: `href="#"`, empty `href`, `javascript:void(0)`, a `<button>` with no handler, nav items pointing to routes/anchors that don't exist.
- Before finishing, mentally walk every link and button and confirm its target exists.

## What you must NEVER do

- Do not change `package.json` dependencies without first confirming with the user — adding a new lib forces a slow container rebuild.
- Do not touch `next.config.ts`, `tsconfig.json`, `drizzle.config.ts`, or any `Dockerfile.*` — those are owned by the orchestrator template.
- Do not write `.env` files. Secrets live in the orchestrator's keystore and reach the container via env.
- Do not write to `/app/data` or other paths outside the repo — the container is read-only outside the source tree.
- Do not call external APIs that need keys without first announcing the env var name to the user.

## How a typical request looks

User: "Добавь страницу учёта расходов: список + форма добавления."

Good response shape:
1. One short sentence acknowledging the plan ("Создаю таблицу `expenses`, страницу `/expenses` со списком и серверным экшеном").
2. `<file path="src/lib/db/schema.ts">` — extend with `expenses` table.
3. `<file path="src/app/expenses/page.tsx">` — server component listing rows.
4. `<file path="src/app/expenses/actions.ts">` — server action with zod validation.
5. End with a one-line "готово, посмотри в preview" — no large summaries.

## Performance budget

- Pages should render server-side in <100 ms (one DB round trip max for list views).
- No N+1: when listing items with a relation, use `db.query.X.findMany({ with: { ... }})`.
- Images go through `next/image` with explicit width/height.

This document is loaded into the system prompt every time the user touches this project — keep your edits consistent with the rules above.
