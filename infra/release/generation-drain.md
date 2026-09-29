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
