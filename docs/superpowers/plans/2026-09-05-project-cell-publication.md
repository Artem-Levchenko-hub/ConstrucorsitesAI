# Project Cell public publication

## Outcome and accepted boundaries

Publish a real MAX app without converting the owner's preview into a public URL.
The user explicitly requires the published app to remain available while the agent
edits and builds its next version. Preserve installed libraries, project-scoped
PostgreSQL and declared business-data volumes. No regeneration or model calls.

Production is a controller-owned sibling of the editable workspace: its own
network, signing secret, managed MAX core and persistent database. Source/rootfs
and home are release-specific; no writable mounts are shared with the editor.
First publication seeds data from a verified immutable environment. Later releases
and process recovery never restore that old data over the live production DB.

An explicit release switch may briefly restart the product process. Editing does
not. An incompatible schema/data contract fails closed before replacing the active
release. Arbitrary schema migrations need a separately proven migration path;
never infer permission to erase or replace customer data.

## Implementation ownership

1. Publication backend/service: dedicated production layout, validated one-time
   seeding, durable operation journal, admission, readiness, code-only switch and
   rollback, restart reconciliation. `services/cell_publication.py`,
   `services/published_machine_backend.py`, associated schema/tests and opt-in
   additions to `machine_adapter.py`.
2. Public MAX boundary: trusted first-launch bootstrap; real signed MAX session;
   rejection of preview identities and forged headers; canonical public origin.
   `services/machine_boundary.py`, managed MAX webhook and behavioral tests.
3. Launch UI: idle is not queued work; actual operation identity; stable retry key;
   refresh-safe progress and bounded polling. No prerequisite preview start.
4. Primary: exact accepted-candidate/snapshot/proof gate, authenticated API dispatch,
   orchestrator routing/startup, bot credential rotation/disconnect, settings
   propagation, integration, independent review and complete production delivery.

## Test-first acceptance

- Missing/red/mismatched proof, active editing, wrong owner/project/snapshot and
  stale source are rejected before publication effects.
- No previous deploy returns idle; a click starts one operation. HTTP uncertainty
  reuses its key; reload cannot create a duplicate or reset the polling deadline.
- First publication retains source data; write new production data, edit source,
  republish compatible code and recreate the runtime: both records remain.
- Failed candidate, incompatible schema or nginx/TLS failure leaves prior release
  and data intact. Recovery never imports a previous DB archive into live volumes.
- Anonymous first navigation receives only trusted login bootstrap. Anonymous API,
  wrong/expired/cross-project credentials, preview sessions and forged identity
  headers fail closed. Signed non-owner MAX entry works; cross-user access fails.
- Owner preview remains private. Tokens are delivered only to trusted managed
  core; generated code and logs never receive platform secrets.
- Settings update legal/support pages independently of generation, including the
  published managed core. Token rotation/disconnect invalidates old public auth.

## Delivery gate

Run focused failing tests before each slice, then relevant full Python/TypeScript
gates, a real isolated Docker/PG publication flow and browser first-entry checks.
Review the full stable diff independently. Commit intended files only, push
`HEAD:main`, deploy that exact SHA from `/opt/omnia` using compose project `full`
and `/opt/omnia/apps/llm-gateway/deploy/full/docker-compose.yml`; never the dev
stack. Recheck active generations and back up before activation. Confirm the
orchestrator, API, worker, web, HTTPS entry and DB/auth user path. Report any
remaining verification limitation explicitly.

## Starting evidence

Baseline `ebeae3e2527212907bfa51678eaae5a1f7201d0a`, local/upstream equal.
No-deploy GET falsely returned `queued`; UI skipped POST and polled indefinitely.
Portable publication was explicitly rejected. Legacy deploy cannot carry portable
rootfs/project PG. Owner-preview restore can import old PG; it is not production
recovery. Source and public lifecycles therefore remain separate.
# Explicit recovery limits for this release

- Code-only updates require identical schema and declared data-store contract. A schema change is rejected before replacing the public release; no implicit destructive migration engine.
- An interrupted first database seed with ambiguous existing target volumes fails closed. Operator recovery must prove the target was never public before clearing/reseeding; ordinary retries never overwrite these volumes.
- Project deletion durably fences source mutations and disables public ingress/recovery before deleting owner/bot records. It cancels undispatched owner wakes, retains verified source/database archives, removes source/public containers and networks, and releases CPU/RAM reservations only after compute removal is proven. Retained business volumes and archives still consume disk; there is no automatic destructive purge of backups. Uncertain deletion effects use exact replay, then a higher-fence observation and a bounded destroy retry. New generation/preview wake remains blocked throughout deletion.
- A real MAX client launch still needs user verification. The automated canary uses disposable signed initData and verifies the same managed authentication and isolation paths without creating a real bot subscription.
