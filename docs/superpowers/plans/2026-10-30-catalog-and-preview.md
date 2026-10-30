# Catalog and preview refinement

The user's annotations extend the account-interface work. Keep the first account release separate and complete its delivery while preparing this follow-up in parallel.

## Required behavior

- Preserve the phone body 410×902 and WebView 390×844. Reclaim header, padding and disabled-footer space; do not change iframe identity, source, authorization, acceptance or recovery logic.
- Adding a content item prepends a new stable ID, keeps all old values and relative order, focuses the new title once, and stops at 100 total items including hidden ones.
- «Сохранить изменения» performs the ordinary save. It does not open or start AI. «Изменить экраны с ИИ» is a separately named explicit action with the existing second confirmation, quotas, version and idempotency guards.
- Explain that a connected catalog takes title/photo/price/item changes without AI or another publication. An unconnected catalog still requires its existing explicit one-time integration.
- The catalog photo area is a keyboard-accessible button opening existing upload and URL controls. Opening it must not pick/upload/save automatically. Preserve pending-upload, draft/cancel and version-conflict behavior.
- Use compact, responsive catalog cards, 136px photo bands, 16px corners, 44px controls and padded select arrows. Preserve all information.
- Replace the services list's mismatched table header with meaningful provider rows and existing state/action information; preserve connection/implementation handlers and unique capability information.
- Remove the publication's long optional-settings explainer and its unused styles. Keep all readiness/consent/bot/address requirements and actual optional service links.
- No pricing, schema, order-history, backend, dependency or business-data changes.

## Execution and evidence

- [x] Implement phone chrome reduction with existing geometry and resize/iframe regression.
- [x] Implement prepend/focus/cap and preservation regression.
- [x] Observe RED for missing photo button (5 failures) and new save/AI labels (13 failures).
- [x] Implement separated save/AI affordances and photo controls using existing mutation paths.
- [x] Run focused MAX suites, full typecheck/lint, CSS parse and independent review.
- Final focused evidence: 16 files / 275 tests passed with two workers; full typecheck/lint passed (one existing apple-icon warning); four CSS files / 404 rules parsed. Sol/max independently reviewed the MAX changes and the later service/publication additions with no blockers. Legacy test selectors were updated to the deliberately renamed AI action.
- [ ] Commit/push only after the first account release; require exact-revision Linux CI, canonical web-only deploy and fresh health/retained-order evidence.
- [ ] Capture authenticated browser screenshots when its tool returns. Until then, record visual QA as unavailable rather than infer it from unit tests.
