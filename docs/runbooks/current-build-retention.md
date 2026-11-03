# Current build retention

The policy keeps current editor/accepted and published environments, persistent
business data, source history and every unresolved recovery dependency. It adds
no user rollback or historical rebuilding feature. Unknown ownership, schema,
missing checkpoint, corrupt archive or incomplete inventory prevents removal.

## Deployment and host collection

Deploy a reviewed, CI-verified revision through `infra/release/deploy-prod.sh`.
Both Core and Commerce must run that revision before registry collection starts.
Do not substitute the development compose stack for production.

On **each cell host**, first validate and then explicitly install:

```bash
sudo bash /opt/omnia/infra/max-k3s/cells/55-archive-retention.sh
sudo bash /opt/omnia/infra/max-k3s/cells/55-archive-retention.sh --apply
sudo systemctl start yleum-archive-gc.service
sudo systemctl show yleum-archive-gc.service -p Result -p ExecMainStatus
sudo systemctl list-timers yleum-archive-gc.timer --no-pager
```

The existing unit name is retained. Every fifteen minutes it collects addressed,
unreferenced environment archives and local application images with a minimum
two-hour grace. It never invokes Docker prune or removes source/data volumes.
Current checkpoint envelope archives are checked before removing older files.
The workspace producer lock and a fresh inventory protect every actual removal;
Docker removal is non-forced and also preserves stopped container consumers.
Platform/shared images and aliases remain protected.

The private report is `/opt/omnia-runtime/retention/build-report.json`, outside
the live-state metadata census. `status=aborted` and a nonzero service exit need
investigation; an unreadable checkpoint is never treated as an empty inventory.
An image error fails the run. Busy workspaces are preserved for a later sweep.
An unresolved lifecycle also aborts the host archive sweep, including files
whose checkpoint reference may not have been published before a crash. Reconcile
the operation instead of deleting its metadata or bypassing this guard.

Before the first run, archive only the frozen metadata of already deleted test
projects through the separately reviewed, exact-path retirement manifest and
verified backup. Preserve deletion barriers and disabled publication identities.
Do not remove an unknown/new project merely to make the inventory pass.

## Registry collection

Install the registry coordinator **only on Core**. It uses the orchestrator's
existing private `.env`, registry credentials and runtime kubeconfig. It does
not print credentials or put Authorization on process command lines.

The private `/opt/omnia-runtime/retention/controllers.json` declares every
controller. Its initial topology is:

```json
{"version":1,"controllers":[{"name":"core","url":"local"},{"name":"commerce","url":"http://172.197.102.6:8003"}]}
```

The remote endpoint is `GET /internal/build-retention/roots`, authenticated using
the existing internal token. A controller with a different token can declare
`token_file` pointing to an absolute, private, owner-readable file. Do not store
token values in coverage JSON, Git or reports. Add every new controller to the
coverage file **before** admitting its projects. Missing/stale/busy inventories,
changed state or a changed Kubernetes workload inventory skip deletion.

On Core:

```bash
sudo bash /opt/omnia/infra/max-k3s/cells/56-registry-retention.sh
sudo bash /opt/omnia/infra/max-k3s/cells/56-registry-retention.sh --apply
sudo systemctl start yleum-registry-gc.service
sudo systemctl show yleum-registry-gc.service -p Result -p ExecMainStatus
sudo systemctl list-timers yleum-registry-gc.timer --no-pager
```

The nightly schedule is 04:37 Europe/Moscow, offset from the local sweep.
The first observation of an image/tag starts a two-hour grace. A tag newly
pointing at an old image gets a new grace period. All aliases for a manifest
are considered together; one protected reference protects the entire digest.
Only strict `max-app/<project-uuid>` repositories are managed. Unsupported
formats and all platform repositories remain untouched. Built-in Kubernetes
workloads, including init/ephemeral containers and scheduled job templates,
also protect images. Controllers using custom workload CRDs must extend the
inventory adapter before their workloads are admitted.

The maintenance transaction persists a UID/owner-guarded journal, fences public
writes with a read-only registry rollout, waits until old writable pods are
gone, and compares every inventory again. A second authenticated registry
process bound only to `127.0.0.1:5001` accepts addressed manifest DELETEs.
It is stopped and its absence verified before Distribution garbage collection.
GC preserves all surviving manifests and never uses `--delete-untagged`.
Finally the owned read-only change is restored and readiness is awaited.
Current app containers keep running during this operation; publication pushes
retry maintenance errors within their existing operation budget, for at most
five minutes. A registry rollout can briefly delay new pulls/pushes.

Distribution requires stopped writers during physical GC; see its official
[garbage collection documentation](https://distribution.github.io/distribution/about/garbage-collection/).

## Failure recovery and evidence

The coordinator's lifetime lock serializes dry runs, maintenance and recovery.
All journals live under `/opt/omnia-runtime/retention/registry/`, never in live
project state:

- `registry-report.json`: counts, fixed status/error codes and allocated bytes.
- `registry-maintenance.json`: owned temporary Deployment change, while active.
- `registry-seen.json`: immutable digest/tag observation grace.
- `registry-receipts.json`: acknowledged manifest deletion receipts.
- `registry-pending-gc.json`: durable intent written **before** the first DELETE.
- `registry-recovery-report.json`: separate startup/stop recovery outcome.

A lost DELETE response may still mean a manifest was removed. Pending-GC intent
survives failures and permits a later, fully validated GC even with no remaining
manifest candidates. It is cleared only after verified physical GC. Dry runs and
busy skips do not clear it. Never delete these journals to suppress an error.

The service runs `--recover-only` before collection and in `ExecStopPost`.
For manual recovery, stop the timer/service first, then use the same CLI,
environment, state owner and journal directory with `--recover-only`. Recovery
needs no live peer inventory, restores only its own Deployment UID/owner change,
and waits for the restored registry to become ready. UID/ownership conflicts
require inspection; do not overwrite another operator's configuration.

To suspend deletion, stop/disable the relevant timer. Wait for any active service
and verify recovery before further registry maintenance. Suspending a timer
does not restore already removed build artifacts. Keep independent verified
backups; this retention task does not replace or repair scheduled backups.

Acceptance must record the deployed SHA on both controllers, service exit codes,
coverage, dry-run/apply reports, current image pulls and checkpoint/data/source
preservation. Test obsolete/current fixtures in an isolated deterministic
environment without paid AI generations. Report allocated freed bytes separately
from manifest logical sizes and from the earlier one-time test-project purge.
