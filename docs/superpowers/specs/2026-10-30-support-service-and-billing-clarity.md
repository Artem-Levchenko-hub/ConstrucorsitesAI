# Support 6.1/6.2 and understandable billing limits

User scope: fix support paths 6.1 and 6.2 from the supplied MVP report, and the confusing /billing limits screenshot. This extends the ongoing owner-console live acceptance; it does not cancel it. Source report excerpt is in the parent workspace `qa-evidence/owner-completion-20261030/support-6-1-6-2-source.txt`.

## Confirmed requirements

- Keep the current support widget, human operator interface and durable conversation history.
- The user confirmed Monday–Friday, 10:00–19:00 Europe/Moscow; first human response within one working day. Receipt/bot messages are not a human response. Friday after closing produces a Monday deadline.
- Show that schedule and the individual first-response deadline in the widget/thread, acknowledgement and reply emails, Contacts, and relevant support information in the offer. Do not confuse first response with resolution time.
- New tickets receive an acknowledgement with SUP number, time and deadline. This must work for an ordinary customer and an admin acting as a customer; operator notifications must not suppress the customer acknowledgement.
- Admin sees waiting time, time remaining/overdue state, and can find overdue tickets. A human reply closes the first-response clock; status-only changes and bot responses do not.
- Both widget and email to support@yleum.ru reach the same support system. Repeated inbox delivery must not create duplicate tickets/messages. Never auto-import unrelated office mail, auto-replies, bounces or mailing lists.
- Unify support sender/reply identity with the published support address using an actually authorized sender. Do not silently spoof an unapproved SMTP identity.
- Remove the approximately 860 KB inline-logo payload from support messages while retaining Yleum branding. Prefer the existing public logo asset; no new image generation or branding changes are needed.
- Guests can retain/recover their own conversation from the acknowledgement without granting access to another ticket. Use an existing random ticket capability or equivalent narrow, cryptographically verified access. Never authorize a ticket by guessed SUP number or unverified email equality. No raw capabilities in database rows, logs or public DTOs; any delivery payload containing one must be encrypted and scoped.
- No attachment ingestion, staff authentication through email, new helpdesk vendor or external messenger integration in this change.
- Limits show current use and allowed capacity separately. The static limits view has a truthful heading and no misleading selected-period context. Repair the known internal plan label consistently; do not heuristically decode arbitrary user text or change prices/entitlements.

## Approaches considered

Chosen by the implementer: extend the existing ticket domain, transactional SMTP outbox, admin and widget. Use a narrowly configured IMAP importer for support mail, with durable idempotency and normal ticket-domain checks. It preserves one conversation model and current security boundaries without a new provider dependency.

Only adding explanatory text would leave missing acknowledgements, email entry and overdue handling unresolved. Replacing the system with an external helpdesk would require migration and a new vendor, which the user did not request.

## Boundaries and acceptance

New support promises apply to new tickets under the chosen schedule. Do not retroactively declare an old ticket overdue under a promise it never had. Preserve existing tickets, outbox uncertainty semantics, authorization, CAS, rate limits and customer/administrator separation. Inbox transport is explicitly configured, certificate verified and limited to the support recipient; leave it disabled rather than inventing hostnames or exposing credentials when configuration is absent.

Acceptance uses meaningful native database and mail-transport regressions, then a live customer acknowledgement, operator reply, reload/history and one support-email entry using a clearly labelled QA message. Record any missing mailbox authorization honestly. Billing validation includes the exact known malformed internal name and a normal unknown plan name, finite/unlimited capacities, period switching and narrow viewport. Finish with independent review, exact green Linux CI, serial canonical deployment, health and live screenshots.
