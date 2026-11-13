# Generation admission during production releases

Normal `deploy-prod.sh` first installs and probes an owned nginx ingress barrier,
then closes the durable PostgreSQL admission fence before
building/restarting anything and waits for accepted generations, controller
operations, activity leases and restorations to settle. Same-key HTTP retries
may replay their existing run; workers may finish pre-fence work. New generation
and restoration reservations return `generation_draining` (503). The fence is
owned by the full target release SHA. A crashed deployment leaves it closed.

Canonical API maintenance calls use `api-maintenance.py`, a root host launcher,
instead of inserting another Python process into a serving reader's cgroup.
It pins the actual API container/image/PID/start time, namespace descriptors and
root, and checks that incarnation before and after each command. The mount,
network and private PID namespaces are joined; serving cgroup membership is not.
The target must be that PID namespace's init, distinct from the host namespace:
its exit kernel-kills the maintenance command. A separate owned process session
also permits bounded termination of both nsenter and its fork on command timeout.
Runtime discovery uses pinned Docker/PID identity, not an HTTP readiness gate,
so an interrupted rollout can repair its stale registry. The actual `/app/.venv/bin/python`, installed sources,
live environment, network and read-only inventory mount are preserved. No host
dependency environment, copied inventory, new API-image helper container or
reader allowlist is used. Bootstrap and interrupted-release checks retain their
exact committed stdin payload and existing target/capture validation. Runtime
replacement, namespace failure or command timeout fails closed without retrying
the mutation or falling back to `docker exec`.

The executable quiescence gate also checks filesystem publication journals on
both core and commerce using each live controller's PID environment and working
directory. Database zero counts alone are insufficient: queued/building/swapping,
unknown or malformed publication journals block restart. Only the latest known
terminal journal response is accepted; the helper never reconciles or edits it.

## Durable waiters and complete reader inventory

A saved recovery wait is excluded from the generation count only after its
strict supported contract, owner, execution lease, workspace fence and terminal
release/hold receipts have been checked. Provider accounting alone does not
prove that a response receiver has stopped. Corrupt or unsupported saved state,
unknown receiver state and active physical writers keep the release fenced;
the release helper never completes, cancels or rewrites these runs.

The privileged observer inventories all five reader slots: API, worker,
generation-worker and orchestrator on core, plus orchestrator on commerce.
Actual processes, container images, supervisors and every live registration
must agree with the target release and supported contracts. An aggregate worker
heartbeat cannot substitute for this inventory. Reader startup and physical
dispatch stay in standby while the observation is missing, stale or incomplete,
including when new recovery execution is disabled.

`generation-reader-observer.timer` runs the root-owned observer on core. It
publishes the same complete snapshot to both hosts; observations expire within
30 seconds. A failed scan or distribution invalidates readiness instead of
refreshing the age of an older result. The authoritative directory
`/run/yleum-generation-readers` is a read-only directory mount in each of the
three API containers and in each orchestrator's systemd mount namespace. Redis
registration and epoch writes can invalidate readiness, never authorize it by
themselves.

Docker observation retries keep the original scan timestamp and 30-second
expiry. A positively identified missing-object error for a requested full
container ID allows a 100 ms, then 200 ms pause between the existing three
complete census attempts. No partial inspect result or vanished unknown reader
is accepted. Persistent churn, malformed output and uninspectable candidates
still deny readiness. Diagnostics contain bounded error categories, counts and
SHA-256 hashes of stderr and requested/missing ID sets, never raw stderr, IDs,
commands or environment. An unclassified Docker failure does not prove that a
container disappeared or that reader revisions were incompatible.

The canonical release captures the existing supervisors and installs the same
owned `rollout-inhibit.json` on both hosts before replacing a reader. Its exact
target SHA and random owner nonce are stored durably in
`/var/lib/yleum-generation-readers` before the corresponding runtime marker is
written in `/run/yleum-generation-readers`. The observer restores the runtime
inhibit after reboot before publishing a new observation. Any runtime marker
presence or uncertain access blocks mutating reader callbacks and dispatch;
health and registration remain available so the rollout can finish.

Keep both inhibits and the SQL admission fence until the actual target reader
set, matching host snapshots, old supervisor retirement and fresh database and
physical quiescence have been verified. Dormant containers are retained, and
are excluded only with complete stopped/no-restart/no-live-process evidence.
Unknown restart targets or affected helpers block release. Published business
services are not classified as generation writers solely because they exist.

An interrupted release can resume only for its exact saved target and nonce.
During a partial rollout the CURRENT reader set can legitimately be mixed. The
separate CLI `resume-check` only classifies current database quiescence under
the exclusive lock of the existing owning SQL drain. The privileged launcher
must first verify both real root-owned supervisor captures and durable/runtime
inhibits, and execute the classifier from the exact target source. A supplied
JSON receipt is not permission. This read-only check never opens a fence,
creates a drain, supplies CURRENT readiness or bypasses either host's physical
gate. The complete actual TARGET reader set is still required before `end`.

Before SQL `end`, a failure retains the saved captures, inhibits and admission
fence. After SQL `end`, admission is already open: a failed marker clear leaves
the remaining inhibits in place and the release incomplete. Standby remains
local to each inhibited host; sequential marker removal is not an atomic global
switch. Retry the canonical script after inspecting the failure and deployment
lock owner. Do not clear an inhibit by age, invent a new nonce for an unfinished
cycle, overwrite a foreign capture or remove an unknown deployment lock.
After successful target checks, the owning release opens SQL admission and
removes only its own durable marker, then its runtime marker. Any remaining
runtime marker continues to deny execution; the same owner can retry clearing.
Fresh inventory
is an observation of the managed topology, not instantaneous exclusion of
out-of-band privileged processes.

### Resuming an owned full rollout with a repaired operator gate

If a physical gate fails before API replacement or during a partial rollout,
retain the original target and owner. After inspecting that terminal result and
reviewing the repaired control revision, use its exact committed driver from an
isolated, clean checkout:

```bash
infra/release/deploy-prod.sh "$OWNING_TARGET_SHA" \
  --resume-owned-release "$EXISTING_OWNER_NONCE" \
  --control-revision "$REVIEWED_CONTROL_SHA"
```

This is a full API **and web** rollout of the original target. It rejects
`--no-web`, `--web-only`, finalize, gateway, bootstrap, legal-version and
repair-window overrides. It does not take over a different target. The control
checkout HEAD must equal the control SHA and contain no modified, untracked or
ignored files. The exact-driver, complete-diff, main ancestry and latest exact
green CI checks described below apply before entering the deployment mutex.

Under that mutex, before any ingress/SQL begin, owner preparation, capture,
build or restart, the driver requires the existing ingress barrier, both hosts'
matching durable/runtime inhibits, both original captures and the read-only SQL
resume check. The actual API revision must be the original target or its Git
ancestor. Missing or conflicting authority fails closed; this mode cannot
reconstruct a missing peer or derive a new nonce. The normal retry path then
requires both owners again and compares their nonce with the explicit argument
before either capture call. Existing captures remain immutable.

The remaining sequence is the normal full deployment of the owning target,
including fresh physical checks before and after service replacement and the
shared final health/retirement/ending checks. All API, ingress and reader helpers
come from that target; only the standalone publication/physical gate comes from
control. Other local deployment helpers/assets must be unchanged across the
complete allowlisted target-to-control diff. No installed source overlay,
gate bypass, manual ending or unknown-lock removal is permitted. After this
owned release finishes, deploy the control revision through a separate normal
full release to align every runtime with the final pushed revision.

### Finalizing an already replaced target with a repaired operator gate

An exact target can be fully running while its final read-only physical gate
fails because a host process disappears during observation. A new product
release cannot take over that target's saved owner. The narrow continuation is:

```sh
infra/release/deploy-prod.sh "$OWNING_TARGET_SHA" --no-web \
  --finalize-owned-release "$EXISTING_OWNER_NONCE" \
  --control-revision "$REVIEWED_CONTROL_SHA"
```

Run the exact committed driver from the control revision in its repository
checkout, after independent review. Both revisions are full 40-character
pushed main SHAs; the owning target must be an ancestor of the control revision.
The script requires that exact control HEAD and a completely clean checkout
(including untracked and ignored files), verifies its own bytes, and restricts
the complete target-to-control diff to the release driver, publication gate,
their four release test files, the
orchestrator's inspector relay and API's gateway billing deployment contract
tests, and this runbook. It queries GitHub for the latest exact control revision's push-to-main
`CI` run. Missing, queued, failed or mismatched CI denies continuation. The
changed-path restriction is a scope check; independent review must still confirm
that the gate preserves all authority, identity, source and physical checks.

This mode requires `--no-web` and both owner/control flags. It rejects web-only,
gateway, bootstrap, legal-version and repair-window overrides. It does not
build, restart, merge remote source, edit environment, publish images, set GitHub
variables, create a SQL drain, capture readers, install an inhibit, or derive a
nonce. The canonical deployment mutex spans every live preflight and the shared
final checks and ending sequence. All API, reader and ingress helpers remain
loaded from the owning target. Only the standalone publication/physical helper
is loaded from the explicitly reviewed control revision. The live controller
continues supplying its exact installed application sources and dependencies;
no product source is overlaid and no old command result or attestation is edited.

Starting authority must be complete: the existing owned ingress barrier and SQL
drain, both original root-owned captures, and both hosts' durable and runtime
inhibits must all match the original target and nonce. The actual API runtime
must already be the full target. The read-only capture classifier checks SQL
quiescence, and both matching target inventories must prove all five readers
and old-supervisor retirement. Missing authority fails closed; this mode never
reconstructs it from checkpoint JSON or from the current processes.

Target reader verification uses one SSH invocation to core and holds the exact
target module's canonical **exclusive** read-only observer lock across both
normal host validations. This prevents an observer publication between two
individually valid results; comparing different fresh snapshots is still denied.
The committed owning target's inventory source arrives on stdin with its SHA-256
checksum, is verified and loaded only in memory. The core action runs directly
under that single lock; calling its `main()` again would reacquire the same lock.
Commerce receives those same target bytes through its normal `verify-target`
CLI. Its SSH process runs as the original non-root sudo operator, validated by
`SUDO_UID`, `SUDO_USER` and the local passwd entry, with that operator's home and
strict, non-interactive host verification. The privileged observer's restricted
peer identity is not substituted for the operator's full verification transport.

The pair requires complete identical `{verified,digest,epoch}` results and a final
canonical freshness/registration/epoch check while still pinned. The helper has
a 25-second deadline, bounds the remote CLI to 20 seconds, and releases the lock
and reaps its peer on failure. Neither observation timestamps nor the 30-second
snapshot lifetime are changed. Each of the existing 18 retries obtains a new
complete pair; the separate final pair check before ending remains mandatory.
Failures retain the original barriers. No observer is stopped, readiness is not
cached, and no installed source, capture, marker, nonce or authority is rewritten.

The script reads back repository API/worker/generation-worker/controller and
common expected revision variables plus the production environment's common
expected revision. It verifies the unchanged web process against its separately
saved expected revision. It also checks the billing Deployment and its sole ready
Pod against the persisted immutable registry digest. That digest must be a
RepoDigest of the exact target API image, whose image ID must equal the serving
API's image. Legacy billing publication can retain its producer's timestamp
label after root Git discovery failed; a matching timestamp annotation is only
metadata, never proof of code. The content chain and target-matching live billing heartbeat
must independently establish the target. The billing heartbeat retains its
canonical full-SHA or exact twelve-character target-prefix contract; the image
content chain establishes the complete target identity. The shared closing path repeats live
API, both workers, billing, both controllers, gateway health, complete reader
retirement, ingress ownership and composite SQL/publication/physical quiescence
before using the original owned ending sequence.

This is a bounded continuation from an **all-barriers-present** starting point.
The ending sequence remains non-atomic: ingress removal precedes SQL removal,
then each host removes its durable/runtime inhibit. A failure before ending
retains the barriers and permits the same explicit continuation after inspection.
A failure after an earlier ending step succeeds leaves a partial state; this
mode intentionally refuses to treat missing authority as proof of prior success
or to recreate it. Preserve the full result and inspect the remaining owned
state. A separately reviewed recovery procedure is required for partial ending;
do not rerun individual `end`/`clear` commands, invent a new owner, edit an
attestation, or clear a remaining marker manually. A successful continuation
finishes the old target; deploying the pushed control revision as a new release
still requires the normal documented delivery loop.

The executable `generation-ingress-drain.py` snapshots the exact vhost and SHA,
validates nginx before reload, probes the live 503 using an unauthenticated
impossible-project request, and refuses to overwrite another release or a later
operator edit. It blocks POST/PUT/PATCH/DELETE only under `/api/projects`, except
the exact generation and restoration cancellation routes. GET/HEAD/OPTIONS,
auth, OAuth callbacks, `/api/runtime/projects/*`, and published app callbacks
remain available. This covers runtime start, deploy, preview-session, sync-kit,
config, delete and restoration apply while the database fence serializes new
run/reservation creation. Already admitted work must still drain; an ingress
rule alone is not proof that earlier external operations have finished.

Both production compose files are mandatory: `docker-compose.yml` and
`docker-compose.hostdb.yml`, from `apps/llm-gateway/deploy/full`, with the existing
project and env. Verify the rendered DB target and known project rows, not only
health. Do not use the development stack.

## First installation (old API has no drain module)

Use the canonical executable path with `--bootstrap-admission` for this first
release, in addition to `--gateway` when the gateway changed. The default path
fails closed on a legacy API; it never silently enables bootstrap. The flag is
rejected when the live API already contains the fence module, and cannot be
combined with `--web-only`.

The script performs these steps itself; do not improvise a manual continuation:

1. Install/validate/probe the owned ingress barrier. Preserve its original vhost
   backup and SHA under `/var/lib/yleum/generation-ingress-drain`.
2. Stream the target revision's **read-only** `bootstrap-check` helper into the
   old API. This path imports no new fence model and reads only existing
   generation/operation/lease/restoration counts. Combine it with the live
   publication journal checks on both controllers. Wait for quiescence under
   the verified ingress barrier; reconcile unknown outcomes, never assume zero.
3. Build the verified target images using both compose files and existing env.
   Recheck ingress and composite quiescence. Run the target API image with
   `alembic upgrade head`, followed by durable `begin` and `check` for the exact
   target SHA, before restarting the serving API or controllers.
4. Continue the normal canonical service rollout. After API replacement the
   script switches to the durable DB gate. Verify exact API/controller runtime
   revision, health, DB identity and composite quiescence before reopening.
   Remove only the owned ingress barrier, then the owned durable fence.

A stopped bootstrap can be retried with the same SHA while the old API is still
serving; ownership and original-config checks remain enforced. If the new API
already serves, resume through the normal path without `--bootstrap-admission`.
The bootstrap read-only check by itself is **not** an admission fence; the script
always verifies ingress before using it.

If rollout fails or returns to an older API, **retain the ingress fence**: a
healthy older process still ignores the database row. Reopening then requires an
explicit, reconciled rollback decision and verified quiescence, not merely a
green health endpoint. Report the deployment incomplete while either fence is
intentionally retained. Never clear another release's row.

## Recovery commands

Use the root-controlled host `api-maintenance.py` entry point to run the
drain module against the exact live API revision. Resolve it first with
`--describe-runtime`; do not use `docker exec` to start another Python process
inside an admitted reader container.

```
python -m yleum_api.services.generation_deployment_drain status <full-sha>
python -m yleum_api.services.generation_deployment_drain check <full-sha>
python -m yleum_api.services.generation_deployment_drain end <owning-full-sha>
```

`check` exits 75 while work remains or the fence belongs to another release.
`end` must only follow successful health/identity checks or the explicit rollback
procedure above. The helper does not cancel work, mutate project data, steal a
lock, or clear an unknown outcome.

## Quiescence of a lost portable command start

A `ProjectMachine` command can persist `starting` before Docker acknowledges
`exec_create`. A later canonical pause does not manufacture its lost exec ID or
exit code. The original command must remain unknown. An operator may instead
record a separate **current writer-quiescence attestation**, after an authorized
normal pause or completed destroy of that exact workspace, using the CI-verified
pushed release:

```sh
git show "$FIX_SHA:infra/release/generation-publication-drain.py" \
  | ssh max-commerce 'sudo -n /opt/omnia/apps/orchestrator/.venv/bin/python - --record-machine-quiescence <workspace-uuid>'
```

Use the actual controller host, and replace the UUID placeholder. Keep ingress
and durable admission closed throughout recording, both controller gates and
reader capture. The command never stops a workspace or deletes data. It fails
closed unless all of these conditions hold:

- Root acquires the **existing** canonical workspace `flock`, without creating
  or replacing a lock file. The typed controller state has its latest completed
  higher-fence `pause/resources_paused` or `destroy/retained` pair, every lifecycle
  operation is terminal, and both generation
  authority fields are empty. The exact cell, machine and Docker metadata bytes
  are bound to the attestation.
- A portable `command/starting` intent qualifies with no exec ID, result or
  progress. Its producer creates and starts a child exec on an existing
  immutable development-container ID. The reviewed producer, routing, lock and
  state source hashes are explicitly pinned.
- The source-guard lease bundle also supports one `fast_check` request whose
  current phase is the declared `typecheck` task and whose exact pending child
  meets the same intent requirements. Both records must match the manifest's
  current ready epoch, the derived child UUID, command digest and timestamps.
  Both original record hashes are bound to the same retirement receipt.
- The reviewed adapter can also record a `fast_check` request before its
  read-only migration dependency check. The parent must still be in its
  initial phase, with zero progress, unchanged heartbeat and no child, even
  a terminal one. This attests retirement only; it does not clear the startup
  hold or replay the request. Other unresolved requests, adaptation,
  lifecycle, source-write and command-receipt records still block drain.
- A stopped workspace can also be observed after a failed restore of its own
  last pause checkpoint. The original pause and restore envelopes must match,
  and each uncertain restore or observation must have an exact completed
  higher-fence observation of `resources_paused`. Their history is retained.
  The same root physical checks still run; this grants no startup authority.
- No portable workspace container/helper remains. Only the exact owned
  auxiliary cell PostgreSQL/Redis services may remain after pause, with no
  unresolved exec. After destroy, no workspace container may remain.
  Two complete root scans enumerate all cgroup directories and every process's
  task membership. Any Docker cgroup or process outside the full current Docker
  roster, unsupported cgroup format, unreadable path or racing read denies.
  This requires systemd/cgroup v2. The Docker roster and daemon/boot/bindings are
  checked again. These are two affirmative absence checks, not an assertion that
  unrelated host process topology is unchanged.
- The root-only, versioned receipt is atomically published without overwriting
  history under `/var/lib/yleum/machine-quiescence/<workspace>/<binding-sha>.json`,
  with file and directory fsync. Every gate use reacquires the lock and repeats
  authority and physical checks. Wake, state/metadata/source changes, reboot or
  daemon identity change invalidate this **current retired-state** attestation.

This is evidence of current writer retirement, not the missing command outcome.
It intentionally cannot certify a running workspace or unknown container create.
An invalidated receipt must never be edited: a newly authorized retirement and fresh
positive checks are needed to record a new content-addressed receipt.

### Native startup preparation

The reviewed Native successor is a separate complete 19-module source bundle.
It retains every historical bundle and adds the startup journal plus its schema,
cell-resource and receipt dependencies. Presence of the new producer prevents
fallback to an older bundle. Existing schema modules become authority only for
the exact successor that uses them; source hashes remain exact live Linux bytes.

The FULL_BUILD startup successor preserves that complete 19-module closure and
all historical bundles. Only the workspace router and startup helper change:
the existing fenced command records its actual same-content process transition
alongside the compiled-asset and migration receipts. This adds no container,
process or lock producer. Mixed old/new pairs, partial closures and unknown
hashes remain unsupported. The API validates this separate FULL_BUILD protocol;
it does not grant restoration or general environment-change authority.

The platform-bootstrap v2 bundle remains supported unchanged. The Next type
preparation successor extends that complete 20-module bundle to 21 modules,
binding the adapter and its fixed guest helper together. The existing command
fence, child journal, deadline and termination own this work; it adds no new
container, startup or lock authority. Unknown, partial or mixed source bundles
remain unsupported. The helper refreshes only generated route types for the
supported MAX Next contract; source, ready build assets and business data retain
their existing checks.

The bootstrap recheck successor keeps that complete 21-module bundle and
updates only the adapter. The fenced bootstrap verifies an already journaled
platform migration without stopping the product. A first application still
uses the existing quiesced SQL path and both paths retain their bound receipt.

The source-guard retry successor binds the adapter, startup history checker and
cell resource manager together. A temporary reader denial allows up to 30 seconds
to start a retry of the applied migration post-check under the same workspace
lock. An in-flight check retains its own operation limits. The guest command is
never replayed by that check. An existing protection hold
returns a bound pre-effect conflict before starting a new ensure mutation.

### Recovering a stopped fast check after source verification failed

The source-guard recovery bundle changes only the startup checker. A receipt
can cover one exact failed `fast_check` operation. The controller still checks
pending writers, restoration, ownership and the original failed result. The
next build verifies the current database and source through normal preparation.

Use the command on the workspace's controller host after its release has
finished. Supply the exact live revision, workspace and failed operation.

~~~bash
sudo -n /opt/omnia/apps/orchestrator/.venv/bin/python \
  /opt/omnia/infra/release/record-source-guard-recovery.py \
  --release-sha <live-40-character-sha> --workspace-id <workspace-uuid> \
  --operation-id <failed-fast-check-uuid>
~~~

The default invocation verifies the database identity and hashes of applied
migrations on an isolated temporary copy. The original PostgreSQL volume is
mounted read-only for copying. The temporary resources are removed before the
command returns. Repeat with `--record` to perform the same checks and write
the receipt. Only a successful receipt allows a new normal generation attempt.
Keep the failed operation, source and database intact. A mismatch, pending
writer or unverified cleanup leaves the hold in place.

Lease observation validates the same history when it finds a settled
`fast_check` failure. Releasing the old lease still requires stable physical
observations of the retained image, volumes and processes.

The command prints the temporary resource label before creating it. If the
operator process is killed, PostgreSQL receives a stop signal after 90 seconds
and is force-stopped five seconds later if needed.
Use `docker ps -a --filter label=yleum.scratch=<printed-stem>` and
`docker volume ls --filter label=yleum.scratch=<printed-stem>` to find any
remaining temporary resources. Check their labels and remove the exact
temporary containers before the exact temporary volume. Confirm that the
filtered lists are empty before rerunning the command.

Quiescence attestations still bind the actual observed source bundle. After a
controller source change, a retired workspace needs a fresh content-addressed
attestation under closed ingress and durable admission, using the documented
`--record-machine-quiescence` command above. Preserve the old attestations and
unknown command journals; neither their outcomes nor their source bindings may
be rewritten to match the new release.

`project-machines/<workspace>/startup-prepares/*.json` can describe an uncertain
**container create**, even with no Docker object and no active SQL lease. An
`intent`, `dispatched`, malformed, foreign or unsafe-path record denies drain.
It cannot be covered by a lost child-command attestation. Terminal records are
strictly validated against the reviewed producer contract; they establish only
historical completion or explicit recovery, never a current runtime proof.

Both global release inspection and root attestation inspect these journals.
Attestation uses only the exact workspace while holding its existing lock, binds
nonempty terminal membership/content, and repeats the check before publishing.
Changing that history invalidates the receipt. Empty historical bindings retain
their original format. A pending record in another workspace still blocks the
global release gate.

A normal pause stops the draft PostgreSQL/Redis services as well as draft
compute. Before pausing a published QA workspace, positively verify its declared
live runtime, database, Redis and boundary dependencies are separate: publication
placement, workload/Pod/Service bindings, both Endpoints and EndpointSlice sets,
network policies and persistent-volume/node bindings. Unknown or shared origins
are a blocker. The allowance for exact auxiliary services in a quiescence receipt
does not promise that pause keeps them running. Check public availability and
business-data preservation separately after the operation.

None of these receipts admits a new generation or resolves an ambiguous failed
restoration. Legacy failed REQUEST records without immutable startup roles and
SQL-indeterminate lifecycle history keep their separate recovery requirements.

### Replacing a blocked candidate before runtime replacement

If a prior owned deployment stopped **before its first persisted reader capture
or rollout inhibit**, ff-merge, environment changes, image build or service
replacement, its candidate can be
rolled back explicitly after the new protocol positively establishes composite
quiescence. Preserve its result/log and verify that no deployment process/lock
remains, the served revision and production configuration are unchanged, no
reader capture or durable/runtime rollout inhibits were installed on either
controller, and every relevant health/identity check passes. An inhibit-free
state alone is insufficient: capture publication precedes inhibit installation.
Hold the canonical deployment mutex across these checks and both owner-end calls.
Check ownership of both prior barriers; run the old SQL check and the CI-verified
new publication/physical gate on **both** controllers. Then use the documented
ingress `end <old-owner-sha>` and SQL `end <old-owner-sha>` commands, in that order.
Immediately run the canonical `deploy-prod.sh <new-pushed-sha>` normally; it
recloses admission and repeats every gate. Any new admitted work is drained by
that normal protocol. Never remove a foreign marker/row, skip a failing gate,
apply this procedure after runtime replacement, or infer rollback readiness from
health or an empty SQL count alone.
