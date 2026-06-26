# 12. Vibecoding & uniqueness plan — kill the cookie-cutter, make every build unique + delightful

> Deep-dive plan for the owner's complaint: generated apps come out **identical and
> templated** ("точь в точь, шаблонно, топорно"), and a "messenger" can't actually
> message. Goal: users **vibecode** with pleasure and get a **unique, beautiful,
> working** app every time. Grounded in real prod forensics, not theory.

## Evidence (project 63a8f8c8 "семейный мессенджер", prod logs 2026-06-26)

- **template = `nextjs_entities`.** Entities generated: `Chat, Message, FamilyMember,
  Photo, PhotoAlbum, Event, Task` — rendered through the SAME `CrudResource`
  skeleton (dashboard + tables) as every other app → "точь в точь" the previous one.
- **The chat page is `<CrudResource entity="Message"/>`** — a *table of message rows*,
  not a conversation. That is why "попереписываться не получится": you can't chat in
  a CRUD table. The engine's only views are table/gallery/board/calendar/master-detail
  — **there is no chat view**, so the writer literally could not render a real chat.
- **`build FAILED: chat/page.tsx TS2739 missing columns, fields`** yet
  `looped-but-serves → reported as done` → shipped typecheck-broken as "Готово".

## Root cause: the entity engine is a CAGE

Three structural causes of "всё одинаковое и не работает":

1. **Same skeleton.** Every prompt collapses to entities + `CrudResource` → the same
   dashboard shell + the same tables. No per-app structure.
2. **Same skin.** `globals.css` (palette, fonts, radius, density — the whole visual
   identity) is a **FIXED file baked into the container image**, never generated
   per-app (`runtime.py`: "baked in, never committed to project git"). So every app
   ships the identical shadcn-default look. This is the single biggest "looks the
   same" cause.
3. **Same crude UI primitives.** Only CRUD views exist; rich interactions (chat,
   feed, editor, board-of-cards with real UX) are not expressible → "топорно".

Plus the trust-killer: "serves ≠ correct" let a red build ship as «Готово».

## The plan — 4 pillars

### Pillar 1 — HONESTY (shipped, commit `914e9e6`)
Never report «Готово» on a red typecheck. The build now runs a real typecheck after
the loop; on red it says «почти готово, осталась ошибка X — нажми Починить» and keeps
the fixable card. No more "делает вид что работает". Combined with the earlier
`crud-resource` crash guard (`d467622`) and the «Починить»-writes fix.

### Pillar 2 — REAL CHAT: a chat VIEW in the entity engine
Add `view="chat"` alongside table/gallery/board/calendar. A messaging entity
(`Message` with `body`, `author`, `createdAt`, a conversation ref) renders as:
conversation bubbles (own vs others aligned), a composer that POSTs a new row
(optimistic insert), auto-scroll, empty state, and **near-real-time** via short
polling now / SSE when the realtime stack lands. Wire it into `CrudResource`'s view
switch + teach the brief/prompt to choose `view="chat"` for messaging entities.
**Effect:** "messenger" apps actually let people message, and look nothing like a CRM.
*(Contained: one view component + a prompt rule. No new stack required for v1.)*

### Pillar 3 — UNIQUENESS via per-app DESIGN DNA (highest visual leverage)
Generate a **theme per project** — palette (brand + accent + neutrals), font pairing,
radius, density, shadow level, hero treatment — derived from the niche/prompt (a
warm rounded family app vs a cool sharp fintech vs a bold editorial blog). Inject it
as CSS-variable overrides into `globals.css` via the **existing style-patch endpoint**
(the managed override block). Same skeleton, **completely different identity** every
time. Add a small curated "design-DNA" library (e.g. 12–20 distinct, tasteful theme
archetypes) and let the art-director model pick + perturb one so output is varied but
never ugly. *(Contained: a token generator + one injection call at build.)*

### Pillar 4 — UNLEASH bespoke surface (structure variety)
Let the model author **distinctive non-CRUD pages** (hero/landing, a custom feature
screen, a unique dashboard layout) instead of only `CrudResource`, so two apps differ
in STRUCTURE, not just skin. This is the docs/11 unleash path; keep it honest by
gating bespoke pages through the functional gate (it must still work). Pillars 3+4
together are what turn "шаблонно" into "уникально".

## Research findings (deep-research `wvcbfe9yf`, folded in 2026-06-26)

A 4-angle cited pass that **audited our own code**. Sameness has THREE independent
root layers — fix all three, not one:

- **Model layer — RLHF mode-collapse + typicality bias.** Aligned models lose output
  diversity and reach for the clichéd average regardless of input (Kirk et al, ICLR
  2024, arXiv 2310.06452). Driver is preference-data typicality bias; a *training-free*
  fix — **Verbalized Sampling** (ask for N candidates **with probabilities**) restores
  1.6–2.1× diversity (arXiv 2510.01171).
- **Design layer — baked-in defaults = the AI-slop signature.** indigo/purple +
  Inter + three-icon grid (Tailwind's `bg-indigo-500`, Wathan's public apology). A
  literal default-color artifact users detect on sight ("beige internet").
- **Prompt/product layer — bare prompt → median, and template/CRUD lock-in.** "build
  a landing page" returns the median of scraped tutorials; builders that fill a fixed
  template or reduce everything to CRUD scaffolds (app.build) buy reliability with
  homogeneity — **Omnia's exact entity-engine situation.**

**Our specific root causes (cited to our code):**
1. Container apps (`nextjs_entities/fullstack/spa`) **bypass the design/vision gate**
   (`messages.py:3830`) and had the art-director kit **stripped** (`prompt_builder.py:3615`)
   — the most "app-like" outputs are the *least* art-directed → same dashboard+tables.
2. The strong model writes only a prose brief; cheap `deepseek-v4-pro` (no reasoning,
   **no vision** — never sees its render, `config.py:842-843`) transcribes it. Theme
   tokens are **advisory prose, not an enforced contract** → regresses to indigo/Inter.
3. **No diversity driver:** no per-project design-DNA pre-sampling, no reference seed,
   no Verbalized-Sampling. The old composition-floor rewarded ONE silhouette; de-funneling
   it to advisory removed the floor but added no variety → ships median with no bar.
4. **`USE_DESIGN_JUDGE` is inert** — defaults True but absent from prod compose env
   (no `env_file`), so the judge that could pick the most-distinctive-of-N never runs.
5. Brittle byte-exact edit matcher (`file_extractor.py:968-971`, `={7}` separator)
   silently drops edits → iteration feels broken ("делает вид что чинит").

### Uniqueness levers (ranked, fold into the pillars above)
1. **Per-project Design DNA stage (highest leverage, cheapest).** Before code, the
   art_director emits a STRUCTURED JSON theme: archetype (editorial / brutalist / luxe
   / retro-futuristic / warm-organic / technical-mono…), font **pairing with weight
   extremes** (100/200 vs 800/900), one **dominant + one sharp accent** (not a timid
   even palette), radius/shadow/spacing scale, layout archetype, motion. Persist as
   authoritative `:root` CSS variables injected into the writer step, **sampled at warm
   temperature seeded by project id** so it differs run-to-run. **Wire it for CONTAINER
   apps too** (reverse the kit strip at `prompt_builder.py:3615`). → Pillar 3.
2. **Deterministic anti-generic guard before the writer runs.** Ban Inter/Roboto/
   system fonts + purple-gradient-on-white + three-icon-grid at the token layer
   (resample on hit); Anthropic avoid-list + positive-extreme rules (3× size jumps,
   layered/geometric backgrounds) verbatim in the art_director prompt. Cheapest
   tell-removal, no retrain.
3. **Curated archetype/reference deck (12–20)** rotated per project by hash so
   consecutive projects never share a look (also fills the empty `REFERENCE_CEILING`
   corpus). Optional user input "**Style: like App A meets App B**" → interpolate two
   anchors instead of one median prior.
4. **Verbalized Sampling + wired judge on the DESIGN layer only.** art_director emits
   3 candidate DNAs with self-rated probabilities (each from a different archetype);
   **wire `USE_DESIGN_JUDGE`** (add to both prod compose blocks / add `env_file`) to
   score on a diversity-aware 1–5 rubric and pick. Keep the BACKEND scaffold rigid —
   "rigid for correctness, high-variance for presentation."
5. **Section-wise generation for container apps** (hero/nav/primary view/detail/
   empty-state as independent themed units) so the dashboard+tables silhouette varies
   section-by-section, with per-section one-turn refinement. → Pillar 4.

### Vibecoding-delight moves (cited)
- **Fix the silent-drop edit matcher first** (cheapest delight win): `={7}`→`={6,9}`,
  tolerant anchored/fuzzy SEARCH, **escalate a miss to a strong model / whole-file
  rewrite**, and an **honest edit chip** (only "applied" when bytes changed).
- **Real correctness gate for container apps**: TSX/TS/JSON parse-check + BLOCKING
  `entities/*.json` schema validation (repair/reject, not warn-only) + a **real
  backend endpoint + seeded admin** so entity apps don't "404 on use." Stop silent
  prod build-error suppression.
- **Protect the loop + trust**: refresh preview once per completed step (no
  mid-write flicker), every prompt a reversible checkpoint with a file-diff receipt +
  one-click rollback, **never bill the user for the AI's own failed builds/retries**
  (the #1 resentment toward Lovable/v0 — a clean RU-market differentiator), surface
  deploy failures in the UI.
- **Surface intent**: wire the existing `ClarifyDialog.tsx` to ask 1–2 questions only
  when genuinely ambiguous; converge refinement in 2–3 iterations.

**Differentiation as a KPI:** the whole v0/Lovable/Base44 category is converging on
beige-internet output users detect on sight — "does NOT look AI-templated" is a
marketable wedge. Own the diversity mechanism in-pipeline now.

## Sequence (dependency order)
1. Pillar 1 honesty (shipped).
2. Pillar 3 design-DNA token generator + injection — biggest "не одинаково" win, contained.
3. Pillar 2 chat view — makes messaging apps actually work.
4. Pillar 4 unleash bespoke pages — structural variety, gated.
5. Route messaging prompts → chat view / realtime stack (intent understanding).

## Standing risks
- Design-DNA must be curated/perturbed, not random — random themes regress to ugly
  (the old composition-floor over-corrected to one safe silhouette; the answer is a
  library of *good* archetypes, not a free-for-all).
- Chat view's "real-time" is polling until the realtime stack (docs/11) is deployed +
  routed; set that expectation.
