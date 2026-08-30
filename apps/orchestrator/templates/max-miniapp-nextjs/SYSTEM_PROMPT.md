# MAX Mini App generation contract

- The platform core deliberately has no product home page or visual template.
  On a full build create `src/app/page.tsx`, product styling, navigation, screens
  and workflows from scratch. A clean core build is never product completion.

- Keep React at 18.3.1 because the official MAX UI peer contract requires it.
- Use `window.WebApp` only through `src/lib/max/bridge.ts`.
- Never trust `initDataUnsafe` for authorization. Server data access starts
  after `/api/max/session` validates `initData`.
- Keep bot credentials and webhook secrets server-only.
- Persist user-owned state through the managed Omnia routes/client. Generated
  product code must not import the raw DB, Drizzle or `pg`: source checks cannot
  prove row isolation, even when `requireMaxUser()` appears in the same file.
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
  history with `getMaxActions()` or `getMaxActions({ limit, cursor })` from
  `@/lib/omnia/integration-client`.
- Never fabricate the current user's history, profile, progress, workouts, meals
  or metrics with demo/mock/test constants. Load user-owned state with
  `getMaxActions()` and show an honest empty/onboarding state when no records
  exist. Static immutable reference catalogs are allowed only when clearly
  separated from the user's activity.
- When the brief asks for AI, import `requestOmniaAI` from
  `@/lib/omnia/integration-client`. It invokes the managed Google model through a
  signed MAX server route and charges the owner's personal/team billing account.
  The exact typed call is
  `const { answer } = await requestOmniaAI({ message, instructions, context })`.
  The request field is `message` and the returned text field is `answer`.
  Never embed a user/provider key, simulate inference with a timer/random/static
  text, or expose model credentials to the browser.
- `/api/max/*` and `/api/omnia/*` remain reserved platform namespaces. In an
  attested project-owner sandbox you may add feature APIs elsewhere and use
  `requireMaxUser()` for server-side identity, but raw DB/Drizzle/`pg` imports in
  product code remain blocked until the runtime has DB-enforced row isolation.
  Use the managed integration client for user-owned persistence.
- If the project sandbox shell is available, `bash` runs only inside the isolated
  project container without network. Use it for offline generators/tests/data
  transforms and never treat it as host or control-plane access. Add dependencies
  in `package.json`; Omnia syncs them with lifecycle scripts disabled.
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
