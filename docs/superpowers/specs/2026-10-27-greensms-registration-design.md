# Green SMS registration delivery

The user selected Green SMS to replace SMS Aero. The existing product decision is
unchanged: registration collects email, phone and password; phone ownership is
mandatory once, email proof optional after phone proof; later login and recovery
use email/password and email respectively. This is a provider migration within
that approved flow, not a new authentication design.

## Approach

Add a small provider-neutral delivery facade and a Green SMS HTTPS adapter.
Retain the existing Aero adapter only for explicit backward-compatible selection;
never fall back or retry a paid send automatically. Replace all readiness imports
together, so account access and OTP requests report the same selected provider.
Directly replacing Aero would couple authentication to another provider again;
an SDK is unnecessary because the existing httpx transport supports the API.

Green SMS uses POST https://api3.greensms.ru/sms/send with Bearer authentication,
form fields to/txt/from and hidden=code. Accept a successful request_id receipt;
the message uses the provider's fixed confirmation template `Ваш код подтверждения: {code}`.
explicit 4xx rejection is failed, transport/5xx/unreadable receipt is ambiguous.
Do not log credentials, phone, code or provider bodies. Preserve durable pre-send
claims, no network I/O under the budget lock, five attempts, ten-minute TTL,
one-minute resend floor, all rolling quotas and unique verified phone identity.

## Rollout and acceptance

Use an existing account token from the authorized provider UI, keep it outside Git
and install it only in the API environment. Publish accurate provider disclosure.
Do not activate the mandatory gate for all users before real delivery is ready.
The current Green SMS cabinet reports test mode and no approved sender; activation
and contract requirements are external prerequisites, not evidence of delivery.
The manager offered a no-monthly-fee authorization-only template route or an
8500 RUB/month individual sender route. The user must select the tariff; do not
activate a recurring fee. Confirm six-digit template support before live rollout.
No contract acceptance, paid sender purchase or outgoing support message is
implied by this design. A single recipient-approved SMS is the live acceptance.

Verify transport rejection/ambiguity/redaction, each quota, resend invalidation,
confirmed-phone login/recovery, signup availability and existing edit scenarios.
All destructive DB tests use disposable CI PostgreSQL. Commit, push, independent
review, green exact-head CI, canonical production deployment and health receipts
are required. Record any provider activation blocker explicitly.
