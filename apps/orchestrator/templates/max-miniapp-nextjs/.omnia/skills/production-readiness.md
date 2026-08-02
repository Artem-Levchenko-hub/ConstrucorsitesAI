# Production Readiness

This is not a template. The output must be usable by real people immediately
after publication and connection inside MAX.

## Real account path

- `MaxAppProvider` validates signed MAX `initData` server-side and creates or
  refreshes the `max_users` record on first open. Use `useMaxApp()` for the real
  profile; never add email/password auth or manufacture a user.
- Preview identity exists only on localhost and Omnia preview hosts. Production
  without valid MAX launch data must show the managed secure-entry error.
- Every user-owned read and write goes through `getMaxActions()` and
  `createMaxAction()` or another managed integration. Data is filtered by the
  verified `maxUserId`; never filter by a browser-supplied id.

## No test data in production

Do not ship `demo*`, `mock*`, `sample*`, `fixture*`, `seed*` or `fake*` user
records, history, metrics, orders, bookings, workouts, progress or messages.
First use renders a truthful empty state and a real creation action. Static
business content comes only from `omniaMaxConfig.content` or a managed catalog.
Examples may appear only as clearly labelled instructional copy, never as
executable records or saved data.

## Functional completion

- Every primary CTA awaits a persisted operation and renders pending, success,
  retry and idempotent repeat behaviour.
- Reload the app and prove the saved result returns for the same user while a
  different verified MAX user cannot see it.
- Validate offline/slow network, empty account, expired launch data, server error
  and duplicate tap. No action may end in a decorative toast with no state change.
- AI uses managed `requestOmniaAI`; payments and catalogs use managed integration
  clients; bot and webhook behaviour remain server-owned.

Before `done`, run build, authenticated runtime check and signed preview review.
Report unavailable infrastructure honestly; never call a static shell complete.
