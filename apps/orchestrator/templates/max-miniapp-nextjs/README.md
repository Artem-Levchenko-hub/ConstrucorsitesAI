# MAX Mini App

Production-ready Yleum template for a Mini App inside MAX messenger.

The scaffold includes the official MAX Bridge and MAX UI, server-side launch
data validation, a signed HttpOnly session, owner-scoped user records, a
secret-protected idempotent webhook and a MAX Bot API adapter.

For local browser development the home screen uses a clearly labelled preview
profile. Real user data is accepted only after the server validates MAX
`initData`.

Yleum-managed production foundation:

- structured business profile and no-code catalog in `src/lib/omnia/max-config.ts`;
- privacy, terms and support pages with owner details;
- verified MAX session and user-scoped actions, consent and analytics APIs;
- idempotent webhook with `/start`, help and open-app bot flows;
- MAX Bridge wrappers for navigation, share, storage, contacts and haptics;
- durable tables for operations, consents, events, notification outbox and audit.

First-party owner analytics counts received authenticated MAX events. A verified
`POST /api/max/session` records one open per distinct signed launch; retries of
that launch share a receipt. Cookie-only GET resume, owner preview and health
probes do not create opens. Platform collection forwards only a receipt UUID and
category, never the app event properties, profile or launch data. Collection
failure cannot change a committed app operation.

For an explicit app event, `trackMaxEvent("checkout", {}, { eventId })` accepts a
caller-held UUID. Use a new UUID for a new logical event and the same UUID after
an unknown response, including a deliberate retry after reload. Omitting the
option generates one UUID per invocation. The SDK retries transient delivery
once with the identical serialized body and a five-second limit per attempt;
known client rejection is not retried. It adds no browser storage. Do not send
personal data, tokens or launch data in event properties; empty properties are
sufficient for the owner counter.

An empty dashboard establishes no ingestion acceptance. Existing apps must adopt
the current managed files and trusted compiled core, then demonstrate a genuine
MAX launch/event through their own authenticated runtime and owner aggregate.
Local SDK/API fixtures are isolated source proof, not live MAX acceptance.
