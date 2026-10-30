# Support and billing completion plan

Base: 0652a6e99790b0c3ae12d51c74a1e75b696b2b4e. Branch: fix/support-billing-20261030 in the existing isolated owner-console worktree. The native worktree tool cannot create/attach this nested repository; reuse of the existing clean Git worktree preserves the primary checkout. Root owns integration, Git and production.

Spec: ../specs/2026-10-30-support-service-and-billing-clarity.md

## Independent work

1. Billing owner: reproduce malformed qa_internal label and misleading limits context; RED→GREEN shared known-plan presentation, separate use/capacity labels, truthful period controls; run focused web tests, types and lint. No payment/entitlement changes or speculative data migration.
2. Support owner: inspect existing creation/outbox/worker/guest/admin contracts; implement immutable first-response deadline and schedule, acknowledgements, consistent compact mail, secure guest recovery, overdue UI and narrow idempotent email ingestion. Use existing cryptographic primitives and explicit IMAP settings. Root supplies runtime configuration and browser testing. Keep all SMTP ambiguity and authorization safeguards.
3. Root continues the already-authorized new-app MAX acceptance. Do not start a second paid app generation to diagnose an existing provider receipt. No deployment while that app's generation/publication has an active writer lease.
4. Exact failed-run evidence showed the final apparent provider 502 was a local gateway refusal: only 2.565 seconds remained before the API source deadline, less than its 15 second receipt reserve, and no upstream request was dispatched. Preserve the stopped-provider checkpoint path while returning a strict 504/deadline_before_dispatch classification with no ambiguous charge or automatic retry. Verify gateway and API projections together; include gateway in the canonical release.
5. Root maps explicit support-mail settings to API/general worker, excludes them from inherited generation-worker configuration and keeps unset optional start UID valid. Reuse existing verified REG mail credentials inside the production host only. First activation imports new mail; UIDVALIDITY changes must replay/deduplicate rather than skip new arrivals.

## Required regressions

- Working-hour boundaries, Friday evening→Monday, timezone, first human response versus bot/status changes; old tickets without the new promise.
- Customer/admin-as-customer acknowledgement, operator notification, retries/replay and uncertain delivery; compact MIME and correct From/Reply-To.
- Guest recovery cannot enumerate/claim foreign tickets, leak capabilities, or authorize from unverified email equality.
- Inbox exact-recipient scope, authenticated transport, crash/replay/concurrent import, duplicate UID/message, spoofed thread identity, bounces/auto-reply loop prevention and bounded MIME handling.
- Admin overdue display/filter, widget deadline/schedule, guest retention notice/recovery, existing conversation flow and mobile layout.
- Known internal tariff name versus arbitrary ordinary plan names; finite/unlimited capacity; limits versus period-dependent tabs.

## Delivery

Run affected tests/types/lint; independent defect-first review, resolve material findings. Commit intended changes and push without force; exact-SHA native CI including PostgreSQL. Deploy through the canonical production driver after current app writer leases finish, preserving real customer data. Verify revisions, full health, reader inventory/open admission and live support/billing flows. Update the local Russian screenshot report with only observed results and any concrete remaining blocker.

Review found principal-switch guest recovery persistence, wrapped mail capability leakage, skipped mail on UIDVALIDITY reset and silent rejection of HTML-only customer mail. The support owner must close these with regressions before source freeze. The full Windows web run was stopped after the UI finding invalidated its source; reuse focused checks and use exact-SHA Linux CI for the final broad suite, including native PostgreSQL. No unsupported green broad-suite claim.
