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

## Public MAX core cold-start hardening

Actual canaries rejected both dev-server modes: Turbopack exceeded the cgroup
limit, and smaller heaps or webpack still triggered implicit Next child restarts.
Increasing the browser timeout alone therefore cannot repair stable public entry.

The public core is now built once with `scripts/build-public-max-core.sh` from the
existing immutable kit dependencies and current trusted template sources. No
package installation/update occurs. The explicit Docker ignore file excludes
environment files, generated projects and unrelated repository content. The
build-only overlay changes legal/config reads, not signature/session/API logic.

`CELL_PUBLIC_CORE_IMAGE` must identify a local immutable image carrying
`omnia.max-core.protocol=1`. `MachineAdapter._start_boundary` checks its presence
and protocol before any auth rotation or old-core removal. Only the public core
runs migrations then `node server.js`, with bounded V8 and the SAME Docker CPU/RAM
quota. Private preview and agent dependency/database access are unchanged. Image
or command changes replace the core once, preserving the signing key, generated
product and all databases. Authentication can pause during that short replacement.

`apply_core_config` writes a temporary JSON file and atomically renames it under
the existing workspace operation lock. The compiled server reads it per request;
legal bodies and titles use dynamic rendering. Missing/invalid config fails
closed. Exact config and all legal pages must pass HTTP readback. Metadata changes
do not rewrite code, compile or restart the public core. Unchanged config is not
rewritten. Private-core source overlays retain their old behavior, except for
bounded file comparison that avoids unnecessary rewrites.

Before creating/reusing ingress, startup verifies health, config/legal pages,
session rejection and unsigned actions rejection. Readiness never creates a
user; session expects401 with a bot and503 after supported token revocation.
Unsigned actions remain401 and the private preview-session endpoint remains404.

Delivery: build `omnia-max-public-core:<pushed-SHA>` with the checked local kit
image ID, set `CELL_PUBLIC_CORE_IMAGE` to its resulting immutable ID in the
orchestrator environment, then activate the canonical full deployment. Keep the
previous environment/image for rollback. Never retag the agent's template image.

`scripts/smoke_public_core_startup.py` is the focused regression canary. Use the
deployed immutable core/PostgreSQL/guard image IDs and a private `--qa-parent`.
It creates only labelled temporary resources on an internal network, with its
own tmpfs PostgreSQL and disposable bot secret. It exercises the real adapter,
one fresh core plus three process restarts, first signed login after readiness,
data persistence and cross-user isolation, and 20 unchanged reconciliations before
the restart cycles (to expose memory growth early).
It also rejects silent Next child restarts during unchanged recovery. It records
cgroup memory/OOM evidence and verifies config plus legal body/title changes
without a core restart. It deletes only its own resources. No
real bot, product data, nginx route or publication journal is changed.

The browser login has a bounded 60-second session request and a 10-second cookie
roundtrip check using portable AbortController. Cold canary and browser tests are
required; healthy containers or a warm-only request are not acceptance evidence.

Verified isolated result (2026-09-06, Next 15.5.22, unchanged 768 MiB / 0.2 CPU):
four first signed logins took 135 / 276 / 294 / 202 ms. Twenty reconciliations
retained the same Next worker; peak memory was 78.7 MiB with zero OOM events.
Metadata and legal body/title changes appeared without restart. A saved record
survived three core restarts and remained inaccessible to another user. The full
orchestrator gate passed 1070 tests (13 skipped, 15 expected failures); eight
browser checks and independent read-only review passed. Actual iPhone/MAX launch
remains a separate device acceptance check after production activation.
