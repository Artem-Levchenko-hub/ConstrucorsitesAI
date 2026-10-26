# Yleum support tickets

## Goal and scope

The user requests a permanent question-mark support launcher, subject/message
submission, immediate visibility for every administrator, an email to each
administrator, a support queue with New / In progress / Done, and a less prominent
archive for completed tickets. Preserve conversations and match the existing
Yleum interface. Source scenarios: user-provided `yleum-testovye-scenarii (3).html`,
6.1 (saved conversation and a human response) and 6.2 (an acknowledged request).

The user explicitly allows guests to submit with a contact email. The chosen
scope is the website form, administrator email notifications and customer reply
notifications. The separate question about ingesting incoming support@ mail is
pending; it is not silently included. No AI support bot, paid model call, payment,
customer-project change, attachment upload or external helpdesk is required.

## Architecture decisions

- Build within the existing API/web and PostgreSQL, using existing Redis and SMTP.
  An external helpdesk would require a new vendor/account and would not give the
  requested native admin queue. Email-only handling would not persist the site
  conversation or provide the required real-time status.
- A dedicated support outbox shares the existing SMTP transport and branded
  renderer. Do not reuse the payment-specific email table or widen commerce
  billing-worker privileges. The general worker's maintenance thread delivers
  support mail, using its own SQLAlchemy engine for that event loop.
- Reuse the production WebSocket path family, with a separate support hub and
  channel authorization. PostgreSQL is the source of truth; Redis events only
  invalidate/refetch state. Reconnect and a bounded fallback refresh recover
  missed events. Email delivery never delays ticket acceptance.
- Archive is derived on the server: Done with resolved_at <= UTC now - 24 hours.
  Lists enforce that filter even if a worker is stopped. History is never deleted
  by archiving. A customer reply reopens the ticket and clears resolved_at.
- Primary owns Git, migrations, integration and deployment. API and web file sets
  may be implemented independently against the contracts below. Preserve the
  canonical checkout's unrelated changes.

## Customer and staff experience

- Mount the accessible fixed `?` launcher in global Providers, outside page scroll
  containers. Use a portal dialog, safe-area spacing, a 44px touch target, Escape,
  focus trapping/return, no horizontal overflow at 390px, and existing MAX/account
  design tokens on the dialog portal itself.
- Registered users see their ticket history and conversations. Guests provide an
  email and explicit support-data consent, get a ticket reference and can continue
  in the same browser. A per-ticket random capability, kept only by the browser
  and stored only as a hash on the server, authorizes guest reads/replies. Never
  put it in URLs, logs, email subjects or analytics. Clear guest/private query
  caches when the authenticated account changes.
- Preserve a failed submission's draft and request ID. Retrying does not create a
  second ticket/message. Show a receipt reference only after the server accepts it.
- Admin center gets Support and Settings tabs, retaining keyboard tab navigation.
  Support shows New / In progress / Done, search, pagination, the complete thread,
  reply and status controls. Taking a ticket assigns it to that administrator.
  Settings -> Support archive is the secondary route for archived conversations.
- Mail deep link: `/admin/max?tab=support&ticket=<UUID>`. Validate URL tab/section
  values, support back/forward, and preserve unrelated query parameters. Settings
  archive: `/admin/max?tab=settings&section=support-archive`.
- Every active existing admin account receives a new-ticket/user-message email,
  including bootstrap allowlisted admins. An admin reply emails the ticket's
  contact address. Staff content is escaped; customer messages are plain text,
  never executed HTML. Guest contact addresses are not matched to existing users
  to grant account access. A staff reply is visible immediately in the thread.

## HTTP and real-time contracts

All routes use the existing `/api` base and error conventions. Datetimes are UTC
ISO strings; IDs are UUID strings. Subject is trimmed 1..160 characters, messages
are trimmed 1..8000 characters. Email uses existing email validation. source_path
is a relative path only, with query/fragment removed and a 512-character maximum.

`GET /api/support/context` ->
`{ user_id: string|null, email: string|null, is_admin: boolean }`.
An inactive/expired/ephemeral principal is treated as a guest for this context.

`POST /api/support/tickets` body:
`{ request_id, subject, message, email?, privacy_accepted?, guest_token?, source_path?, website? }`.
For a registered account, owner/email come from the session, not client claims.
For a guest, require email, consent and a random 64-hex guest_token generated by
the client before submission and reused for retries. website is a honeypot and
must be empty. Return a TicketDetail. Store an actor-bound unique request key;
reuse with different content returns 409. Never return or persist a raw guest token.

`GET /api/support/tickets?limit=50&offset=0` -> `{ items: Ticket[], total: number }`
lists only the registered caller's own history, including archived tickets.
Guest history references/capabilities are local; each detail uses the token header.

`GET /api/support/tickets/{id}` and
`POST /api/support/tickets/{id}/messages` (body `{ request_id, message }`) require
the registered owner or, for a guest ticket only, `X-Support-Token`. Missing/foreign
tickets return the same 404. These are customer endpoints; staff use admin routes.

Admin routes, all guarded by existing AdminUserDep:

- `GET /api/admin/support/tickets?status=new|in_progress|done&archived=false&search=&limit=25&offset=0`
  -> `{ items: Ticket[], total: number, new_count: number }`.
- `GET /api/admin/support/tickets/{id}` -> TicketDetail.
- `POST /api/admin/support/tickets/{id}/messages` body `{ request_id, message }`.
- `PATCH /api/admin/support/tickets/{id}` body
  `{ status: new|in_progress|done, expected_version: number }` -> TicketDetail.
  Stale version returns 409; changes are audited without message bodies.

Ticket fields: `id, reference, subject, status, version, requester_email,
created_at, updated_at, resolved_at, archived, source_path,
assigned_admin_email` (staff only; otherwise null), `message_count,
last_message_preview`. TicketDetail adds `messages`.
Message fields: `id, author: customer|support, body, created_at`.
All mutation responses return TicketDetail. A customer reply to Done reopens New;
a staff reply takes a New ticket into In progress. Repeated request IDs with the
same actor/content are idempotent and do not duplicate mail or events.

`/api/ws/support`: exact same-origin/canonical allowed-origin validation as other
platform sockets. Browser sends first JSON frame `{type: "subscribe"}` for a
registered session, or `{type: "subscribe", ticket_id, guest_token}` for a guest;
capabilities never enter the URL. The initial frame has a short timeout and size
bound. Server sends `{type:"support.ready"}` then invalidation messages
`{type:"support.changed", ticket_id, version, reason:"created"|"message"|"status"}`.
Session/active status/admin authority are rechecked at least every 15 seconds.
Do not expose all ticket metadata on a customer/guest channel. The client pings
within 25 seconds and reloads REST state on ready/reconnect. One event listener
owns cache invalidation; duplicate ticket/version events do not repeat toasts.

## Persistence, delivery and safety

- Add ticket, immutable message and per-recipient email-outbox tables, indexes,
  unique request/event keys and relational checks in one additive migration after
  the verified current head. Ticket public reference uses a unique database number.
- Row locks serialize thread/status writes. Transaction rollback leaves neither
  the message nor email rows. Commit precedes Redis publication. Redis failure
  never loses the ticket or creates a duplicate on retry.
- Recipients follow `role=admin OR lower(email) IN admin_emails_set`, with active
  persisted users only. Revalidate admin authority immediately before delivery.
  The contact address is captured for this ticket, not a grant to another account.
- Outbox claim uses SKIP LOCKED and committed sending state before SMTP, stable
  Message-ID, retry only when existing EmailDeliveryFailed.retry_safe permits it;
  ambiguous delivery/stale sending becomes uncertain and is never blindly resent.
  A stopped SMTP server does not block ticket creation or real-time handling.
- Own rate-admission buckets: durable distributed ticket creation limits (per
  account/contact/IP and a bounded global window), separate reply limits and
  honeypot. No reset-password bucket reuse. Hash IPs rather than retain raw IPs.
  Validate CSRF origin for cookie-authenticated mutations and WebSocket origins.
- No raw contacts, messages, capability tokens or SMTP replies in diagnostic logs.
  Existing user deletion cascades owned support data; archive does not delete data.

## Acceptance evidence

Prove two normal users cannot read/reply to each other's tickets; guest token
cannot access another ticket; non-admin cannot use admin routes; revoked staff
role/session loses live access. Prove all persisted and bootstrap admins get one
outbox item, no inactive admin gets it, rollback/retry/parallel claims do not
duplicate, SMTP outage/ambiguous results are safe. Test archive boundary and
reopen, pagination/search, plain-text escaping and draft/idempotency behavior.

Run focused tests against a disposable PostgreSQL, API Ruff/mypy, web test/lint/
types/build, migration sanity and the exact pushed SHA full CI. Obtain independent
read-only review, fix material defects, push configured upstream, deploy only the
canonical production driver and prove service/revision health. Live desktop and
390px acceptance covers customer submit, two admin views, reply, reconnect/refresh,
archive with controlled test timestamps and no cross-user disclosure. No real
payment, customer data deletion or AI generation is required.
