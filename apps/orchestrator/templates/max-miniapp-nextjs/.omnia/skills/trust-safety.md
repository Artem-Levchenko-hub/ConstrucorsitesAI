# Trust & Safety

This is not a template. Apply proportionate safeguards where user harm,
irreversible action or sensitive data is possible.

## Data and identity

- Authenticate with validated MAX `initData`; use the managed session and never
  trust client-supplied identity or `initDataUnsafe` for authorization.
- Collect the minimum data needed for the immediate task. State purpose before
  optional contacts, marketing, payments, health or precise personal data.
- Persist explicit consent through the managed consent endpoint with policy
  version and a reversible choice. Do not bundle optional consent into access.
- Never place personal data, provider keys or raw launch data in analytics,
  client logs, screenshots or error copy.

## Payments and irreversible actions

Show merchant, item, amount, currency and cancellation/refund expectations before
payment. Use idempotency, provider-confirmed status and recoverable pending states.
Require confirmation for deletion, purchase, publication or consequential
changes; make the effect specific rather than using a generic warning.

## User content

Provide report, block and moderation states when content can be shared. Separate
draft, published, hidden and removed. Do not imply automated moderation is human
review. Protect empty and error states from leaking another user's content.

## Health and medical information

Distinguish wellness guidance from diagnosis. Explain uncertainty and source of
recommendations, avoid emergency or medication claims outside supported scope,
and present a clear route to qualified help when risk is plausible.

## Safety proof

Test unauthorized access, cross-user reads, duplicate submission, stale payment,
revoked consent, deleted content and retry after network failure. Fail closed for
authorization and payments; fail helpfully for ordinary product errors.
