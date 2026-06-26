# Skill: security — write code the SAST gate won't reject

Read before any server action, route handler, form, or data/auth-touching code.
These are the highest-severity mistakes in AI-generated code (arXiv 2510.26103);
the **SAST gate blocks them deterministically**, so following this is not
optional — a violation fails the build and you'll be sent back to fix it.

## Data access — through the SDK/engine ONLY
- Reach data via `@/lib/sdk` or `@/lib/entities/engine`. **NEVER import
  `@/lib/db`, `drizzle-orm`, or `pg`** in your own files — that is the only way
  to run a query without the auth/ownership/membership scoping, and the backend
  guardrail rejects it before ship.
- Authenticate first: call `requireUser()` / `getCurrentUser()` at the top of any
  server action or custom route that touches data; pass the user through.

## No injection (CWE-89 / 78 / 94)
- **SQL:** never build a query by string interpolation/concatenation. Use the
  SDK/engine (parameterized for you). `` `SELECT ... ${x}` `` is rejected.
- **Code:** never `eval(...)` or `new Function(...)` on anything that isn't a
  hard-coded literal you wrote. There is always a real construct instead.
- **OS commands:** don't shell out from a web app. No `child_process`
  `exec`/`execSync` with interpolated/concatenated input.
- **XSS:** don't use `dangerouslySetInnerHTML`. Render via the kit/JSX; if you
  truly must render user HTML, it must be sanitized first — but prefer not to.

## No hard-coded secrets (CWE-798 / 259)
- Never put an API key, password, token, or private key in source. Read from an
  env var: `process.env.MY_KEY`. If you need a new external key, NAME the env var
  in chat and STOP — do not invent a value, do not write `.env`.
- The gate flags real literals assigned to secret-named vars (and known token
  shapes like `AKIA…`, `sk-…`, `ghp_…`). `process.env.X` and placeholders are fine.

## Authorization is server-side
- Owner-scoped data is filtered by the engine — never filter by user yourself and
  never trust a client-supplied user id.
- Gate protected pages on the SERVER (`requireUser()` in the route-group layout),
  not only with a client `auth.me()` check. Role pages: `requireUser({ role })`.

Golden shape of a custom server action:
```ts
"use server";
import { requireUser } from "@/lib/session";
import { entities } from "@/lib/sdk";
export async function approveOrder(id: string) {
  const user = await requireUser();              // authn first
  const order = await entities.Order.get(id);    // engine scopes it
  // ...business logic THROUGH the sdk/engine, never raw SQL...
}
```
