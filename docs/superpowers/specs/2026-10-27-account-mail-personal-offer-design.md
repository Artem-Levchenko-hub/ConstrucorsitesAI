# Account security mail and one-off payment-test offer

User requests: branded password/reset/email-change mail; one personal payment/email test for 1 RUB to manually verify payment notifications. User explicitly chose one personal purchase, not a public price change. Base: 3e7799ed7b2dee4f55de4f43528137a2fb172b8e. Public Pro v4 is 2990 RUB; historical accepted contracts remain immutable.

## Account flow
- Login is email. Existing reset tokens stay hash-only, single-use and 30 minutes; changing password revokes all sessions. Reset and signed-in change share a transaction helper and enqueue a completed-event notice atomically.
- GET `/api/account/credentials` returns `{has_password}`. POST `/password` accepts `{current_password,new_password}` and returns `{ok:true,reauthenticate:true}` under that prefix.
- POST `/email` accepts `{current_password,new_email}`; returns 202 `{status:"confirmation_sent"}`. Existing login remains valid until old and new addresses are both confirmed. Both expiring hashes bind old email and session version; resend supersedes; stale verification/reset challenges are invalidated on completion. OAuth provider-subject links remain unchanged.
- Links use `/confirm-email-change#token=...`. Page removes fragment after reading into memory; explicit confirmation button posts `{token}` to `/api/account/credentials/email/confirm`. GET never consumes. Response `{status:"pending"|"completed",reauthenticate:boolean}`. Do not sign into an account automatically or overwrite another principal's browser state.
- New `EmailChangeChallenge` and `AccountSecurityEmail` models. The durable success outbox contains frozen recipient, event kind/key and timestamps, never passwords or confirmation/reset capabilities. General worker owns safe SMTP retries, uncertain delivery and stable Message-ID.
- Use the existing logo/theme/HTML+text/CID renderer. Success alerts contain generic recovery/support actions. OAuth-only users set a password through existing recovery. Forgot responses remain generic 202 even on SMTP failure; preserve rate limits and log no secrets.

## Personal payment/email test (final user clarification)
- The user already has Pro and explicitly requires **only a 1 RUB payment and email test**: do not change any existing subscription, billing period, renewal preference, balance, credit grants/reservations, packages or limits.
- Public Pro v4 remains2990RUB and accepted historical contracts remain immutable. This is an individually issued payment-test offer, not a discounted subscription purchase or renewal.
- `BillingPurchaseOffer` binds one billing account/user to1.00RUB for seven days and one successful use; it has no plan/normal-price relationship. `billing_offer_payments` preserves attempts.
- Only a privileged operator issues the specific user's offer through `issue_billing_purchase_offer(session, *, user_id, issued_by_user_id, now=None)`, without implicit commit. No public/admin-self grant endpoint or coupon framework.
- Authenticated GET `/api/billing/purchase-offer` returns optional `{id,price_rub,currency,expires_at,reserved_payment_id}`. POST `/api/payments/test` accepts `{offer_id,idempotency_key}`. Client cannot select amount or another owner. Explicit `Payment.purpose=payment_test` prevents ordinary subscription fulfillment.
- Provider/payment/receipt amount is1RUB. Success consumes the bound offer and enqueues a clearly labelled branded payment-test notice through the normal billing SMTP infrastructure. No subscription activation/expiration/renewal/credit side effect; ordinary strict activation price guards stay unchanged.
- `email_notifications` gains `payment_test`; only this kind may have null subscription/period metadata. Existing notice kinds retain required metadata through a conditional database constraint.
- Concurrent and different-idempotency retries reuse the pending reservation. Indeterminate provider state is never released for another charge. Definitively failed/cancelled attempts may retry. Only one success; cross-user IDs never grant access.
- UI states: 'Проверочная оплата1₽' and 'Действующая подписка и пакеты останутся без изменений'. This payment creates no recurring agreement; do not change an existing subscription's auto-renew setting.

## Acceptance and boundaries
- No actual payment, SMS, paid generation or real user's credential change by the agent. The user makes the 1 RUB purchase manually after deployment. Validate through mocks and isolated PostgreSQL, then live quote/UI/mail observation.
- Test wrong password, expired/replayed/superseded challenges, uniqueness/concurrency, old+new confirmations, session revocation, rollback and SMTP failure/uncertain delivery. Test cross-user offer denial, concurrent checkout, retry/recovery, amount/receipt/notice validation, one success, byte-for-byte unchanged subscription/credit/limit snapshots and unchanged public catalog.
- Primary owns Git, additive migration0098, model/router/worker/CI integration and canonical production deployment. API worker owns account mail/backend/templates; pricing worker owns offers/checkout/billing UI; account UI worker owns account forms/confirmation page. No new dependencies.
- Review independently, run required full CI for the exact pushed revision, deploy via `infra/release/deploy-prod.sh`, verify exact runtime identities/health and representative desktop/tablet/mobile UI. Preserve unrelated work.

Security references: https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html and https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html . No private source was sent to documentation services.
