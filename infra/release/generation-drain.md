# Generation admission during production releases

Normal `deploy-prod.sh` first installs and probes an owned nginx ingress barrier,
then closes the durable PostgreSQL admission fence before
building/restarting anything and waits for accepted generations, controller
operations, activity leases and restorations to settle. Same-key HTTP retries
may replay their existing run; workers may finish pre-fence work. New generation
and restoration reservations return `generation_draining` (503). The fence is
owned by the full target release SHA. A crashed deployment leaves it closed.

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

Inside the running API image:

```
python -m yleum_api.services.generation_deployment_drain status <full-sha>
python -m yleum_api.services.generation_deployment_drain check <full-sha>
python -m yleum_api.services.generation_deployment_drain end <owning-full-sha>
```

`check` exits 75 while work remains or the fence belongs to another release.
`end` must only follow successful health/identity checks or the explicit rollback
procedure above. The helper does not cancel work, mutate project data, steal a
lock, or clear an unknown outcome.

## Quiescence of a lost portable child-command start

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
- Coverage is limited to the original portable `command/starting` intent with
  no exec ID/result or progress. Its producer only creates/starts a child exec
  on an existing immutable development-container ID; it cannot create a
  container. Request/adaptation/lifecycle/source-write/command-receipt unknowns
  retain their existing denial. The reviewed producer, routing, lock and state
  source hashes are explicitly pinned. Changes require a protocol review and
  manifest update; newly observed source is not automatically trusted.
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
