# Project Cell Phase 1: safe pilot capacity

**Status:** executable rollout prepared from a read-only production baseline on 2026-08-31

**Scope:** release enough memory for the single-cell pilot without deleting a
container, image, volume, database, workspace, route, or rollback artifact

**Architecture contract:**
`docs/superpowers/specs/2026-08-31-enterprise-project-cell-agent-runtime-design.md`

## 1. Measured baseline

The baseline was collected over the existing SSH administration path without
changing the host:

- local `main`, `origin/main`, production Git and all four release health
  endpoints reported `d0433aba640f0ccfdc55bdd23ace44119864b98c`;
- API, LLM gateway, orchestrator and web health returned HTTP 200/`status=ok`;
- the generation-run query returned no `pending`, `running` or
  `cancel_requested` rows;
- the encrypted off-host backup endpoint verified the 2026-08-31 00:15:54 UTC
  bundle, size 426,550,131 bytes, against its recorded SHA-256;
- host capacity: 8 CPU, 16,388,440 KiB RAM, 3,121,152 KiB available RAM, no
  swap, 250,033,156,096 bytes free on `/`, 11% inode use;
- `/dev/kvm` exists, cgroup v2 is active and containerd 2.2.1 is installed;
- six compose projects were running, with 40 running/paused containers and 31
  named volumes;
- Docker reported 188.2 GB of images and 85.33 GB of build cache. Its
  `reclaimable` estimates are inventory only and do not authorize deletion;
- seven idle Omnia dev previews were in Docker `paused` state and retained
  approximately 4.6 GiB combined resident memory; one active dev preview used
  approximately 803 MiB.

The disk and virtualization gates pass. The memory gate fails because the
Project Cell contract requires at least 6 GiB available before K3s installation
and one worst-case pilot-cell soak.

## 2. Root cause and bounded change

The legacy hibernate policy warm-paused paid-tier previews. Docker pause freezes
processes but keeps their memory charged, so the UI and orchestrator called the
projects hibernated while the shared host remained full.

The bounded release changes the default policy to Docker stop for every idle
tier. Stop preserves the container writable layer, environment, mounts,
network/port configuration and bind-mounted project workspace. Wake starts the
same container and reruns its declared command. A dedicated host may explicitly
restore the former latency trade-off with
`HIBERNATE_WARM_PAUSE_PAID=true`; shared/scaling hosts keep it false.

The sweep also reconciles containers left in `paused` by the former policy:
`stop_container` performs the already-supported thaw-then-stop sequence. No
container or filesystem is removed.

## 3. Preconditions

Immediately before deployment:

1. Reconfirm exact pushed/server revision and preserve the known dirty
   production Git files.
2. Reconfirm zero active generations and no release/build/backup/restore/
   project-deletion operation.
3. Reconfirm encrypted off-host backup integrity and record its timestamp,
   size and checksum outside command output containing secrets.
4. Record API, gateway, orchestrator and web health; `docker compose ps`;
   `MemAvailable`; disk bytes/inodes; every dev container state and memory.
5. Confirm production env does not explicitly set
   `HIBERNATE_WARM_PAUSE_PAID=true`.

Any failed precondition stops the rollout. It does not trigger cleanup.

## 4. Deployment and proof

1. Deploy the exact protected-main revision with the documented production
   compose/orchestrator release path.
2. Confirm orchestrator health reports that revision.
3. Observe one sweep interval. Every non-keep-alive legacy `paused` dev
   container must become `exited`; production, published app, database, MinIO,
   Redis, registry and unrelated compose containers must retain their prior
   state.
4. Re-measure host memory. Admission remains closed unless `MemAvailable` is at
   least 6 GiB and existing production health remains at baseline.
5. Wake one owner-approved stopped dev canary through the normal orchestrator
   endpoint. Prove the same container identity/configuration returns to
   `running`, its preview reaches ready/HTTP 200, project files are unchanged,
   and database access still works.
6. Stop that canary through the normal hibernate path and prove memory is
   released again.
7. Run the full production generation/publish canary and delete only its
   normal disposable project through the existing canary cleanup contract.

## 5. Rollback

Set `HIBERNATE_WARM_PAUSE_PAID=true`, restart only the orchestrator through the
documented release path and repeat health checks. Already stopped previews do
not need bulk restart; each normal wake remains valid. No source, workspace,
container writable layer, database or volume restoration is required because
the change never deletes them.

If an individual wake fails, keep its stopped container and workspace intact,
disable Project Cell admission, capture its status/logs, and repair the wake
path. Do not recreate or remove the container until its exact source, data and
mounts are reconciled.

## 6. What this unlocks

Passing this phase supplies protected headroom for the isolated K3s/Kata hello
smoke on the current server. It is not the horizontal scaling mechanism.
Horizontal scale comes from the later Project Cell control plane: durable
database queue, one fenced writer lease per project, off-runner checkpoints,
Kata execution nodes, scheduler placement and storage that can rehydrate a
workspace on another worker.

No user prompt is routed to `cell` during this phase.
