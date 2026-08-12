# MAX Mini App

Production-ready Omnia template for a Mini App inside MAX messenger.

The scaffold includes the official MAX Bridge, server-side launch data
validation, a signed HttpOnly session, owner-scoped user records, a
secret-protected idempotent webhook and a MAX Bot API adapter.
The product runtime is headless. A dormant MAX UI compatibility layer is
enabled only for historical product snapshots that already import that library.

For local browser development the home screen uses a clearly labelled preview
profile. Real user data is accepted only after the server validates MAX
`initData`.

Omnia-managed production foundation:

- structured business profile and no-code catalog in `src/lib/omnia/max-config.ts`;
- privacy, terms and support pages with owner details;
- verified MAX session and user-scoped actions, consent and analytics APIs;
- idempotent webhook with `/start`, help and open-app bot flows;
- MAX Bridge wrappers for navigation, share, storage, contacts and haptics;
- durable tables for operations, consents, events, notification outbox and audit.
