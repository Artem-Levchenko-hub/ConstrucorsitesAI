# Skill: realtime — build a leak-proof, live messenger

Read before any chat / messaging / presence / live-feed / collaboration feature.
A chat is NOT a CRUD table — building it as one is the #1 failure on this stack
(messages leak across users; «messages» become refresh-to-see rows). Follow this.

## Leak-proofing is the whole game (membership ACL — server-enforced)
- A message belongs to a CONVERSATION/CHANNEL and is readable ONLY by its
  members. Never model chat as `owner` (each user sees only their own — wrong) or
  `public` (everyone reads — leak). Use the platform's **members** access:
  a message is visible iff a membership row links the reader to its parent
  channel. The engine enforces this on every read/create server-side.
- Verify it like an attacker: a non-member must get 403 on the channel's
  messages, and must NOT be able to create a message in it. The gate checks this;
  build it so it passes the first time.
- DMs = a 2-member conversation; group = N members; org channel = team members.
  Reference a person by a `userId` string + `<UserSelect>` (a real registered
  account), never a made-up `User` entity.

## Make it LIVE (not poll)
- Deliver via the stack's realtime hub (SSE/WS + Redis pub/sub): publish on
  send, subscribe per channel. Do NOT poll a list endpoint on a timer.
- **Presence via Redis**, not in-memory per node: a short TTL heartbeat key per
  online user + pub/sub on join/leave. (In-memory presence is wrong the moment
  there's more than one process; it's also the most expensive steady-state load —
  keep it to channel-scoped presence, this is corporate/team scale, not Telegram.)
- **Optimistic UI:** render the sent message immediately with a pending state,
  then reconcile when the server echoes it. On reconnect, backfill missed
  messages (fetch since the last seen id), don't drop or dupe.

## Correctness details that make it real
- **Read cursors + unread counts:** store each member's last-read message id;
  derive unread from it. Don't recompute by scanning all messages on every render.
- **History pagination:** keyset (before/after a message id), newest first; never
  load the whole history.
- **Ordering:** server timestamp + id as tiebreak; don't trust client clocks.
- Attachments via the platform upload (signed URLs), not base64 in the message.

Self-check before `done`: a stranger is 403 on a channel they're not in;
sending in tab A appears in tab B without a refresh; presence flips on
connect/disconnect; reload backfills history without dupes.
