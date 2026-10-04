# Bounded custom-route actor isolation probe

**Status:** local probe and authentication/boundary evidence only. The signed U/V deny gate for the actual published `/api/qa-tasks` app and Cell promotion remains **NOT_RUN**. No leak in that app was reproduced, and no product/authentication/DB module was changed.

`tools/qa/custom-route-actor-isolation.mjs` is a standalone Node 22+ runner. It sends GET only, refuses non-loopback targets and redirects, bounds request time/body size, and emits no cookies, launch data, response bodies, row IDs or fixture markers. It accepts pre-issued signed cookies for a disposable public Cell through `identityMode: "cell"` or signed launch headers for a direct trusted template fixture. Public Cell strips `x-omnia-*` headers, so header-only auth is not Cell acceptance.

Run:

```sh
node tools/qa/custom-route-actor-isolation.mjs /private/disposable-isolation-fixture.json
NODE_PATH=apps/orchestrator/templates/max-miniapp-nextjs/node_modules node --test tools/qa/custom-route-actor-isolation.test.mjs
```

The private fixture JSON has this shape (replace placeholders; never commit actual credentials):

```json
{
  "schemaVersion": 1,
  "disposable": true,
  "baseUrl": "http://127.0.0.1:3000",
  "identityMode": "cell",
  "projectId": "DISPOSABLE_PROJECT_UUID",
  "collectionPath": "/api/qa-tasks",
  "actors": [
    {"id":"10001", "cookie":"__Host-max_session=U_COOKIE", "itemPath":"/api/qa-tasks/U_ROW_UUID", "protectedValues":["U_ROW_UUID", "UNIQUE_U_SYNTHETIC_MARKER"]},
    {"id":"10002", "cookie":"__Host-max_session=V_COOKIE", "itemPath":"/api/qa-tasks/V_ROW_UUID", "protectedValues":["V_ROW_UUID", "UNIQUE_V_SYNTHETIC_MARKER"]}
  ],
  "foreignProject":{"cookie":"__Host-max_session=FOREIGN_PROJECT_COOKIE"},
  "foreignOwner":{"cookie":"__Host-max_session=FOREIGN_OWNER_PREVIEW_COOKIE"},
  "ownerPreview":{"cookie":"__Host-max_session=SAME_PROJECT_OWNER_PREVIEW_COOKIE"}
}
```

For a direct template fixture omit `identityMode`/`projectId`; replace actor cookies and foreign-project cookie with `initData` fields. Identity then resumes through `/api/max/session` instead of `/__omnia/identity`.

## Credential acquisition and boundary expectations

For **actual acceptance**, obtain U and V launch data through two distinct MAX test accounts opening the disposable application's bot. Exchange each through the normal same-origin `POST /api/max/session` endpoint on that isolated app and retain its Secure/HttpOnly cookie privately. This preparatory exchange can materialize test users; the GET-only runner does not perform it. Seed unique actor-owned rows only in an approved disposable fixture app using its normal APIs. Never seed/migrate customer `qa_tasks` to enable this check.

Acquire foreign-project and foreign-owner cookies from a second disposable project's normal MAX login and owner preview bootstrap respectively; obtain same-project owner preview through the normal signed preview bootstrap. Preserve their origin/project provenance with the acceptance record. Do not mint arbitrary numeric cookies and label them genuine MAX acquisition. Unit tests intentionally use synthetic HMAC launches/cookies and do **not** count as live acceptance or real MAX client evidence.

| Request | Required result |
| --- | --- |
| U/V identity | 200, exact distinct numeric actor; Cell also exact project and valid epoch |
| U/V own collection/item | 200 JSON, contains own fixture ID and marker, excludes other fixture |
| U reads V item / V reads U item | 401/403/404, excludes both actors' protected values |
| Anonymous, foreign project, foreign owner identity/collection/items | 401/403/404, no protected values |
| Same-project preview identity on public Cell | 401/403/404; cannot satisfy U/V actor identity |
| Preview custom collection | denial or 200 without either actor's fixture |
| Preview custom item | 401/403/404, no protected values |

Three rounds of concurrent collection reads and alternating item reads check request/pool reuse behavior. A denial status cannot mask a leaked JSON/body value; JSON escapes are decoded before checks. POST/PATCH/DELETE authorization, idempotency/ETag, PostgreSQL role/GUC/RLS behavior, actual published revision and mandatory Cell promotion binding remain separate gates. The probe is not enabled as a promotion policy and cannot issue a promotion permit.

## Local evidence at base be9d4c91

- New HTTP harness tests: **10 passed**, zero failures; actual template `validateMaxInitData` and `session.ts` execute. Tests deliberately leak rows/list/404 bodies, escape JSON values, return redirects/oversized bodies, and substitute preview for an actor; probe rejects each. `local-probe-tests.log` contains the exact output.
- Existing real `BoundaryHandler` HTTP and project-role unit tests: **36 passed**; `boundary-role-tests.log`. These are not current published custom-route or real PostgreSQL acceptance.
- Node syntax and Git whitespace checks: pass.
- Existing template full suite with pinned pnpm 9.15 on PATH: **4 passed, 2 failed**, both `tests/database-compatibility.test.mjs` quoted-identifier cases; installed shared Drizzle is **0.36.4**, while package declares **0.45.2**. Installed `pg-core/dialect.cjs` SHA256 is `266f546cbe1ba01d6e395f945ae19c97f891fce0ea7db75da3a3a252b1e398ed`. No dependency repair was attempted. `template-tests-pinned-pnpm.log` retains failures. Earlier outer-pinned execution still selected global pnpm11 in subprocesses and also failed the lockfile error-code check; `template-tests.log` records this environment mismatch.
- No production requests, migrations, customer/provider mutations, model calls, publish, push or deploy. Root owns integration and delivery.

Next falsifying check: run the loopback runner against the exact disposable Cell-served custom-route candidate with actual U/V MAX-acquired cookies, two isolated rows and foreign-project/owner credentials; independently exercise cross-actor writes and runtime DB privileges. Until then the MVP live actor gate remains open.
