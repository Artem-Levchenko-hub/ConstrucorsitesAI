# MAX Mini App generation contract

- Keep React at 18.3.1 because the official MAX UI peer contract requires it.
- Use `window.WebApp` only through `src/lib/max/bridge.ts`.
- Never trust `initDataUnsafe` for authorization. Server data access starts
  after `/api/max/session` validates `initData`.
- Keep bot credentials and webhook secrets server-only.
- Every user-owned table stores `maxUserId`; every read and mutation filters by
  the verified session user.
- Preserve webhook secret verification, request-size limits and event
  idempotency.
- MAX UI is optional. The product owns its visual system, layout and navigation.
- Never add a platform-owned visual shell or persistent legal footer.
- Keep theme contrast, safe-area padding and mobile touch targets usable whether
  the product chooses MAX UI, semantic HTML or its own components.
- Keep `/support`, `/legal/privacy` and `/legal/terms` reachable from an
  app-native settings, profile, about or overflow menu, then mark the product
  root with `data-omnia-native-legal-nav="true"`.
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
