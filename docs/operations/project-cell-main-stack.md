# MAX Project Cell: extensible main stack

The approved scope is Next.js/React/TypeScript with Node22 and pnpm9.15.0 first.
The agent may add compatible libraries, edit package/lock files, install system
userland and use a necessary helper process. It is not a universal-framework
product or a fixed package list. No model generation is part of deployment proof.

## Reachable behavior

- The existing owner-canary provider advertises the capability over bootstrap.
  New empty cells seeded from the trusted template (including byte-identical
  pristine project-directory copies) receive `.omnia/cell.json`
  with `pnpm install --no-frozen-lockfile`, `pnpm build`, `pnpm test`, `pnpm start`.
  The package test script requires real `tests/*.test.mjs`; no passing dummy test
  or product page is supplied. The agent creates `src/app/page.tsx` and product UI.
- Existing no-manifest source stays on the legacy path. An intentional manifest
  transition retires the credentialed legacy draft before the first shared-source
  write. Controller-owned identity then forbids a silent downgrade if the guest
  deletes its manifest or the operator disables the provider.
- The machine has project-only source/home/data volumes and a persistent rootfs.
  It never mounts the old credential-bearing agent home. Root can install guest
  packages, but has no NET_ADMIN/NET_RAW/SYS_ADMIN, Docker socket, host paths,
  privileged mode or platform credentials.
- A separate trusted namespace guard permits only the public dependency proxy
  and explicitly authorized traffic. The proxy resolves and validates every
  destination and connects to the validated numeric address. It denies private,
  metadata, host, configured public platform addresses and cross-cell targets.
- The pristine MAX core owns managed PostgreSQL credentials and signing secrets.
  The gateway owns reserved `/api/omnia`, `/api/max`, `/auth`, `/__omnia` routes.
  Product servers receive trusted user/project/epoch headers, not signing keys.
- Shell/build attempts invalidate preview and completion proofs even without
  source changes and even on failures. Fresh completion checks require the actual
  product root, signed MAX bootstrap/session, protected data, negative auth and
  exact active lease epoch. Segment continuation cannot revive invalidated proof.
- Build/command requests share one aggregate deadline across install/build/test,
  not a fresh deadline per task. API build is600s and shell300s; whole preview
  apply reserves870s for work including capture/readiness plus30s for cleanup,
  with930s transport timeout. Blocking helpers inherit remaining work time:
  they cannot open a fresh readiness/quiesce window at the end of the request.
  A timeout drains in-flight Docker effects before cleanup/fencing, even if the
  drained helper fails. An unavailable Docker daemon can delay that safe drain;
  a transport failure never counts as proof.

## Production configuration and rollout

Use the existing `/opt/omnia` checkout, `full` production compose at
`apps/llm-gateway/deploy/full/docker-compose.yml`, and the host
`omnia-orchestrator.service`. Do not deploy the development `infra/` stack.
The verified production host service reads `/opt/omnia/apps/orchestrator/.env`
with WorkingDirectory `/opt/omnia/apps/orchestrator`. The older
`/opt/omnia-runtime/.env.orchestrator` is a different unused file, not a symlink;
preserve it. Inspect the effective unit/environment again before editing.
Preserve all existing owner-canary lists and
secrets. No expansion to other users is authorized by this feature.

Build the base from the approved immutable Node22 image and pin the output image:

```sh
cd /opt/omnia/apps/orchestrator
docker build --build-arg NODE_BASE=sha256:APPROVED_NODE22_IMAGE_ID \
  -f scripts/Dockerfile.project-machine -t omnia-project-machine:main-stack .
docker build --build-arg BASE_IMAGE=sha256:APPROVED_PYTHON_IMAGE_ID \
  -f scripts/Dockerfile.project-machine-guard -t omnia-project-machine-guard:main-stack .
```

Use real immutable IDs, never the placeholders above. The base includes Node/npm,
pnpm9.15.0 and Python3 for internal command/archive helpers. Node's bundled trust
roots bootstrap HTTPS apt before installing distro CA certificates; TLS and apt
signature verification remain enabled. Guard source is copied with deterministic
readable modes. Neither image includes project or platform secrets.

Add these **host orchestrator** settings (JSON syntax for the CIDR list):

```dotenv
CELL_MACHINE_ENABLED=true
CELL_MACHINE_BASE_IMAGE=sha256:APPROVED_BUILT_MAIN_STACK_IMAGE_ID
CELL_MACHINE_GUARD_IMAGE=sha256:APPROVED_BUILT_GUARD_IMAGE_ID
CELL_NETWORK_POOL=10.253.0.0/16
CELL_MACHINE_DENIED_CIDRS=["170.168.72.200/32"]
```

The pool was free in the scoped 2026-09-03 inventory; recheck all Docker networks
and host routes immediately before rollout. New networks use bounded explicit
/28 allocation and refresh/retry Docker overlap conflicts. Never prune other
networks or change daemon address pools. The public host IP is upstream NAT and
is not discoverable from host interfaces; explicitly deny every relevant public
platform IP in trusted configuration, including future addresses.

API/worker require the changed code but no new machine environment forwarding:
capabilities come from the orchestrator. Preserve existing `PROJECT_CELL_*`,
`WORKSPACE_PROVIDER=docker_owner_canary`, `DOCKER_OWNER_CANARY_ENABLED=true`,
owner allowlists and pinned PG/Redis/backup settings. Rebuild/restart API/worker
using canonical production compose, restart the orchestrator, verify status,
health and canary capability/bootstrap. Record pushed/deployed revision and image
IDs. Do not claim delivery until that loop is complete.

Default resource profile remains unchanged: bundle memory4GiB / CPU2, executor
1GiB / .5CPU. Proxy/guard/gateway reserve128MiB / .2CPU inside that slice, leaving
896MiB / .3CPU for the machine; the core uses the already-accounted draft slice.
PNPM9 tarball workers are reduced to one, lifecycle concurrency1, network4,
Node heap at most512MiB (lower for small budgets), Next build workers1. These are
practical defaults, not package restrictions; aggregate cgroup limits still apply.

## Recovery and limitations

Environment snapshots are private immutable sanitized rootfs images plus named
volume archives with workspace/base/manifest identity, sizes and SHA256 hashes.
All nested artifacts are validated before restoring source/home/PostgreSQL.
Source checkpoints seal the matching environment reference. No log/tmpfs/secrets
enter the captured image config. Restoration is durably fenced until imports and
declared recovery checks succeed. Owned restore/archive helpers are discovered,
identity-checked and confirmed dead before pause, imports and activation.

Declared datastore quiesce failure/pending state cannot be bypassed by retrying a
stopped container. Pause retains that stopped rootfs but does not certify it as a
new checkpoint. Explicit restore can replace the failed state with a fully
validated requested checkpoint; in this recovery case the rollback baseline is
that requested envelope, **not** the unquiesced current data. Recovery failure
remains fenced/degraded. No automatic merge or salvage is claimed.

- CPU/RAM/pids are enforced; disk admission and snapshot byte bounds are **not**
  hard rootfs/volume quotas. Retained artifact history still needs operational
  storage monitoring/retention. No shared-host hostile-tenant qualification.
- Egress is HTTP(S)/CONNECT, not arbitrary raw TCP/UDP. Installers must honor the
  proxy. Private registries and destinations are not automatically authorized.
- Gateway strips product `Set-Cookie`, incoming product Cookie/Authorization,
  spoofed identity/forwarded headers and Upgrade. Arbitrary app-cookie auth and
  WebSockets/HMR are not supported; default Next serves a production build.
- The existing managed PostgreSQL API foundation is preserved, not expanded into
  arbitrary product DB grants, payments, booking/roles or new provider actions.
  Public product publication remains rejected separately (409); it is not part
  of this iteration. Real user/MAX launch and generated product behavior require
  the user's later testing; authored owner-preview tests are narrower evidence.
- Disabling the provider while portable cells exist is fail-closed, not automatic
  cleanup/downgrade. Restore them to an explicit legacy checkpoint or complete
  controlled lifecycle cleanup before disabling. Do not delete controller state.
