# Skills index — nextjs-realtime

> Progressive-disclosure index (knowledge-layer plan §2). Only this index sits in
> the system prompt; read a full `<name>.md` with `read_file` when the task
> matches. Skills RAISE THE FLOOR; the deterministic gates (sast_gate,
> perf_a11y_gate, accept_gauntlet, the membership/leak checks) are the CEILING.

| Skill | When to read it (description) |
|---|---|
| `realtime.md` | Before building chat / messaging / presence / live-feed / collaboration. How to make it leak-proof (membership ACL), live (Redis pub/sub + optimistic UI), and correct (read cursors) — the messenger canon. |

For a messenger, `realtime.md` is mandatory reading — a chat built as an
owner/public CRUD table LEAKS across users and is the #1 failure on this stack.

The universal **security / a11y / perf** canons apply here too (same content as
the nextjs-postgres-drizzle skills). They are enforced regardless by the gates
(sast_gate, perf_a11y_gate); a shared cross-stack skills dir so realtime injects
them as guidance too is a noted follow-up (avoid duplicating the files until a
third stack needs them — Rule of Three).
