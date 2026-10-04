# MAX owner analytics — bounded source verification, 2026-10-04

Base: `be9d4c91d0d37cba46b6c0f3b665eb293430eabe`. Isolated branch: `task/mvp-owner-analytics`. This receipt records local implementation checks. It does not establish live collection or complete the production delivery loop; the parent agent owns integration, push, CI, deployment, compiled-core rollout and live acceptance.

## Behavior and limits

- `POST /api/runtime/projects/{project_id}/analytics` accepts only the existing project/body/path/method-bound signed core assertion. Direct MAX initData alone cannot attest a committed action. The payload contains a UUID receipt and one fixed category (`open`, `action`, `event`); extra fields are rejected.
- The platform stores only project, receipt UUID, fixed category, server receipt timestamp and a project-scoped HMAC actor key. It stores no raw MAX ID, profile, launch data, token, custom event name or business payload. Owners receive counts and daily buckets, never actor keys or individual records.
- `GET /api/projects/{project_id}/max/analytics?days=7|30|90` authorizes the current project's owner before aggregation. Unauthenticated requests fail; another owner's project returns 404. Successful owner responses are private and not cacheable.
- Receipt timestamps use PostgreSQL `timestamptz`. Ranges and daily buckets use the fixed `Europe/Moscow` timezone, including the current partial day. Unique users count distinct actors in the whole range; summing daily unique users can exceed the range total.
- An **open** is successful establishment of a session from validated signed MAX launch data, after the session cookie is prepared. A stable UUID derived from actor + exact initData deduplicates repeated authentication of the same launch. It is not a count of all browser loads: GET session resume and owner-preview renewal do not emit opens. A different signed launch produces another receipt. Client receipt time is not trusted; delayed delivery is counted when the platform receives it.
- Successful action creation forwards the durable business-action UUID, including replay of a previously committed action. Action updates/deletes are outside this MVP counter. Existing event writes remain in the per-cell analytics table; optional `eventId` gives custom-event callers stable retry identity. Legacy event requests without an ID remain separate events.
- The server helper requires `OMNIA_PUBLIC_APP_ORIGIN`, the project's bot credential and a canonical numeric authenticated MAX actor. Preview traffic is excluded. Technical action/event names starting `omnia_health_` are excluded. The collector itself does not claim a release or published-origin attestation; UI copy describes received activity from MAX.
- Two forwarding attempts use the same receipt, each bounded to two seconds. Network/upstream failure does not change a committed user action's successful response. There is no durable platform forwarding outbox or outage backfill in this MVP. App-local records remain authoritative. No fake historical metrics or Metrica-counter inference is introduced.
- Actor HMAC uses the configured stable encryption key, falling back to the JWT key. Rotating the selected key changes pseudonymous actor keys and can split unique-user counts across the rotation.

## Delivery closure

Managed kit version 28 includes `src/lib/omnia/analytics.ts` in the managed and security-locked file set with the project literal pinned. The existing noncompiled core overlay also sends the helper and session/action/event routes together in its single archive. An old BE9 source fixture is upgraded and real Node loads/executions verify the helper import closure; unchanged product source is preserved and repeated reconciliation performs no additional write.

Compiled native public cores skip this source overlay. Updating a product snapshot or synchronizing the kit alone **does not activate analytics in an existing compiled Coffee core**. Adoption requires the normal trusted compiled-core image build, release artifacts and pin rollout on both production hosts, and selection/republication of the updated core for the owned test app. No lock, epoch, database role, provider, billing, generation or shared MAX provider policy is changed here.

## Local evidence

- 11 new API checks pass: signed event → real PostgreSQL aggregate, owner/actor/project isolation, transport idempotency and conflict, Moscow midnight cutoff/buckets, old managed-tree route execution, forwarding failures, and incremental migration up/down/up.
- 86 existing API kit/artifact/auth regressions pass.
- 87 orchestrator overlay/config/recovery/public-boundary checks pass, including the actual old-tree atomic archive regression.
- Full web suite: 107 files, 911 tests pass. Final analytics panel: four checks pass; web typecheck and changed-file ESLint pass. Next production build exited 0 before the final copy/timezone adjustment; subsequent panel checks and typecheck cover that adjustment.
- Frozen pnpm 9.15.0 starter dependencies: TypeScript check and four starter/database contract checks pass. The initial shared dependency directory had obsolete Drizzle 0.36.4; the isolated frozen install uses locked 0.45.2.
- Changed Python Ruff checks and new API service/router/model plus changed orchestrator module mypy checks pass. `git diff --check` passes.
- Full local API pytest was attempted and interrupted around 46% after the shared local overlay exhausted disk and PostgreSQL began reporting `DiskFullError`. Its failures are not fully classified and it is not a passing suite. The final exact merged candidate requires the full CI gate. Only this worktree's completed build output was removed; its private dependency output was moved to `/tmp`. No other agent's files or database were deleted.

Local evidence files are under `/workspace/qa-evidence/`: `mvp-analytics-final-new-api.log`, `mvp-analytics-final-regression.log`, `mvp-analytics-orchestrator-final.log`, `mvp-analytics-web-full.log`, `mvp-analytics-web-build.log`, and the incomplete `mvp-analytics-api-full.log`. Local test environments use dedicated loopback PostgreSQL databases; no real provider/model job or production database was used.

## Remaining live acceptance

1. Integrate into the exact canonical candidate, resolve kit version/route overlap with actor isolation, pass complete CI and apply migration `0076_max_analytics` through the normal production path.
2. Build and stage the trusted compiled-core artifact/pin on both production hosts; activate it for an owned isolated Coffee app without changing its product UI/data. Verify actual deployed core bytes/imports and database/HTTP readiness.
3. Confirm a genuine MAX signed launch by actor A and one durable business action produce owner-visible open/user/action/event increments. Retry the same authentication/action/forwarding receipt and confirm no duplicate increase. Confirm actor B changes unique-user count while another project remains unchanged.
4. Prove GET session resume, owner-preview renewal and technical probes do not increase counts. Test unauthenticated owner reads, foreign-owner reads and forged/foreign-project assertions against the live candidate.
5. Open the owner dashboard and confirm Moscow range/daily values against received events, refresh/error/empty behavior and absence of profiles/tokens/payloads in responses. Record deployed revision, exact artifact/pin, expected/actual counts and next falsifying check. Historical/outage backfill remains outside the claimed MVP.
