# MAX Mini App generation contract

- The platform core deliberately has no product home page or visual template.
  On a full build create `src/app/page.tsx`, product styling, navigation, screens
  and workflows from scratch. A clean core build is never product completion.

- Keep React at 18.3.1 because the official MAX UI peer contract requires it.
- Use `window.WebApp` only through `src/lib/max/bridge.ts`.
- Never trust `initDataUnsafe` for authorization. Server data access starts
  after `/api/max/session` validates `initData`.
- Keep bot credentials and webhook secrets server-only.
- Every user-owned table stores `maxUserId`; every read and mutation filters by
  the verified session user.
- Preserve webhook secret verification, request-size limits and event
  idempotency.
- Use MAX UI controls, theme, safe-area padding and mobile touch targets.
- Use BackButton for nested views and closing confirmation only while data is
  unsaved.
- Request contacts or other sensitive platform data only after an explicit
  user action.
- Do not add Telegram WebApp, VK Bridge, Auth.js or password login.
- Treat `src/lib/omnia/max-config.ts`, `/legal/privacy`, `/legal/terms`,
  `/support`, `/api/omnia/*`, the MAX session and webhook files as
  Omnia-managed infrastructure. Import the config; do not duplicate or delete it.
- Render `omniaMaxConfig.content` as the editable business catalog. The owner
  changes it in MAX Studio without another model call.
- Every primary CTA must perform a real persisted operation through
  `/api/omnia/actions`; never ship decorative buttons or fake success states.
  Product code calls `createMaxAction(actionType, payload)` and reads the scoped
  history with `getMaxActions()` from `@/lib/omnia/integration-client`.
- When the brief asks for AI, import `requestOmniaAI` from
  `@/lib/omnia/integration-client`. It invokes the managed Google model through a
  signed MAX server route and charges the owner's personal/team billing account.
  The exact typed call is
  `const { answer } = await requestOmniaAI({ message, instructions, context })`.
  The request field is `message` and the returned text field is `answer`.
  Never embed a user/provider key, simulate inference with a timer/random/static
  text, or expose model credentials to the browser.
- Never import `@/lib/db` or `drizzle-orm` from product files and never create a
  parallel `/api/max/*` or `/api/omnia/*` implementation. Persist and read
  product activity only through the managed integration client; MAX Studio owns
  authentication, tenant filtering, actions, consent, events, AI and webhooks.
- Persist explicit consent through `/api/omnia/consents` before marketing
  notifications, contacts, payments or other optional personal-data use.
- Track key funnel events through `/api/omnia/events`; do not send personal data
  to third-party analytics by default.
- Support loading, empty, error, retry and success states for every async flow.
- Use the Bot API for start/help/open-app flows and the Bridge wrappers for
  links, sharing, contacts, storage, haptics and the BackButton.
- For sales include visible price/order confirmation/cancellation/refund states.
  For bookings prevent duplicate slots. For loyalty keep a transaction ledger.
  For user content include report/block/moderation states.
- The result is a complete mobile product, not a static mockup or a set of
  disconnected entity screens.

## Product studio quality bar — no visual template

- Before writing code, privately explore three genuinely different art directions
  for this exact brief. They must differ in composition, information density,
  typographic voice, shapes and motion — not merely colour. Choose the direction
  that makes the primary action clearest and gives the product a recognisable
  character. Do not copy a previous generation or default dashboard.
- Persist the selected direction in `.omnia/max-design-spec.json` before product
  code. Keep its product promise, primary action, considered directions, screen
  map, visual system, motion choreography and states aligned with the finished app;
  continuations use this artifact to preserve the project's identity.
- Treat `src/app/page.tsx`, `src/app/globals.css` and new product components as a
  blank design surface. You may completely redesign them. Keep the locked runtime,
  provider and root layout unchanged.
- `globals.css` starts as a minimal reset, not a token contract. Keep the valid
  `@import "tailwindcss"`; place any external font import before it; create your own
  project-specific semantic `--app-*` variables. Never assume `bg-background`,
  `border-border` or another semantic Tailwind utility exists unless you define its
  Tailwind v4 mapping yourself.
- A MAX Mini App is not a landing page. Do not add a marketing hero, feature-card
  wall or pricing section unless the requested product actually needs it. Build a
  focused mobile workspace with a clear current state, primary action and useful
  content immediately visible.
- Give every interaction a complete visual state. Motion is purposeful feedback:
  press, selection, progress, insertion/removal, skeleton-to-content, sheet and
  success/error transitions. Prefer transform/opacity, MAX haptics and reduced-motion
  fallbacks; never rely on hover or continuous decorative animation.
- Do not call `done` while the signed MAX visual review still says broken/generic.
  Apply its concrete findings, rebuild, check the runtime and review the result again.
