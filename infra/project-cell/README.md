# Project Cell Phase 2: isolated K3s trusted-hello foundation

This directory contains read-only Phase 2 validation scaffolding. It validates a
rollback evidence contract, pins K3s inputs, and defines the allowlisted trusted
hello manifest. It does not install K3s or a dead-man, execute rollback, create
Kubernetes resources, change the orchestrator, accept user code, change nginx,
or route production traffic.

> **ALL HOST/KUBERNETES MUTATION IS HARD-DISABLED.** Install apply, rollback,
> dead-man arm/disarm, and trusted-hello apply exit immediately after
> `set -euo pipefail`, before path resolution, dependencies, locks, or writes.
> No orchestrator runtime code is changed by this delivery, and no server command
> in this runbook was executed by this task.

## Exact supply-chain policy

`versions.env` is data only; no shell script sources it. `phase2ctl.py` compiles
the complete expected map and rejects a missing, extra, or changed value.

| Artifact | Pin |
|---|---|
| K3s | `v1.36.4+k3s1` |
| K3s amd64 binary | `sha256:835873f37245fc615f547a2fe2af9402a347875f13fa64a1f136de644955ea3f` |
| Official amd64 checksum file | `sha256:db1dbdc92f0cb5ccd361348a113f4dff82b1f9194175e5993efc37224a04ba4d` |
| Installer at the pinned tag | `sha256:46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad` |
| Trusted hello image | `registry.k8s.io/e2e-test-images/agnhost:2.53@sha256:99c6b4bb4a1e1df3f0b3752168c89358794d02258ebebc26bf21c29399011a85` |

Kata `4.1.0` and amd64 digest
`3dc6b69c4acb787b967b04b64599a20d02a8beb1a8eaab3084110df9d0b08c96`
are next-gate evidence only.

Primary sources:

- [K3s server flags](https://docs.k3s.io/cli/server)
- [K3s configuration](https://docs.k3s.io/installation/configuration)
- [K3s networking requirements](https://docs.k3s.io/installation/requirements#networking)
- [K3s v1.36.4+k3s1 release](https://github.com/k3s-io/k3s/releases/tag/v1.36.4%2Bk3s1)
- [Pinned K3s installer](https://github.com/k3s-io/k3s/blob/v1.36.4%2Bk3s1/install.sh)
- [Kubernetes node reservations](https://kubernetes.io/docs/tasks/administer-cluster/reserve-compute-resources/)
- [Kubernetes official test images](https://github.com/kubernetes/kubernetes/blob/master/test/images/README.md)

## Security boundaries implemented locally

- Evidence is normalized into a content-addressed restore contract containing
  bytes, type, uid/gid, mode, symlink target, and explicit absence. Firewall
  capture or parser failure aborts.
- Off-host and rescue receipts are detached-signature verified against a pinned
  out-of-checkout trust policy. The policy binds verifier identity, public-key
  fingerprint, recovery-certificate fingerprint, production hostname, and
  allowed remote location prefixes. Local, loopback, self-declared, stale, or
  re-pointed evidence is rejected.
- Recipient PEM containing a private key is rejected. Sealed output is bound to
  the bundle id and ciphertext checksum.
- Postflight and rollback schemas can be validated without consuming them in a
  host mutation. They do not enable dead-man arm/disarm or rollback execution.
- The hello manifest hash and schema are fixed and namespace Pod Security labels
  are exact, but this delivery does not apply the manifest or claim a live echo.
- Shadowed `PATH`/`PYTHONPATH` sentinels prove that every mutating entrypoint
  rejects before executing a dependency or creating its state directory.

These are local validation controls only. Future mutation implementations and
their old fake-host acceptance scaffolding are explicitly unreachable and are
not claimed as live or production-safe verification.

## Trust and attestation contract

The production trust policy must be `root:root`, mode `0400`, a regular
non-symlink file, and located at
`/etc/omnia/project-cell/phase2-trust.json`. It has an exact schema:

```json
{
  "production_hostname": "production.example",
  "verifier_id": "independent-recovery-verifier-01",
  "verifier_public_key_sha256": "<sha256 of public PEM bytes>",
  "recovery_certificate_sha256": "<sha256 of recipient certificate bytes>",
  "allowed_offhost_locations": ["s3://approved-recovery-bucket/"]
}
```

Receipts use `verifier_id`, not a self-asserted hostname. Off-host evidence must
use a policy-allowlisted `s3://`, `gs://`, or globally addressed `https://`
location; local paths and loopback/private HTTP endpoints fail closed. Signed
sources, signature, ciphertext, public key, and trust policy are all re-opened
and re-hashed whenever a marker is consumed.

## Closed live gates

The 2026-09-01 read-only audit found production healthy at revision
`ebb7bcc3c33ea9c001a5ab25d75915edae2049e7`, K3s/Kata absent, about 7.7 GiB
available RAM, no swap, and sufficient disk/inodes. It also found many Docker
networks/listeners and no ufw/firewalld/nft executable. These observations do
not authorize installation.

Before live enablement, a separate reviewed change must prove all of the
following and then deliberately remove the hard gate:

1. Exact clean checkout/script bytes and approved revision on the execution
   host; every executable artifact is bound to that revision.
2. Fresh signed backup and active-operation evidence produced outside this
   checkout, reconciled with every API/background/worker path, with zero active
   work while the exclusive maintenance lock is acquired.
3. A real private admin address, no overlap among admin/pod/service, routes, and
   every Docker network, no existing TCP 6443 listener, and restorable firewall
   tooling whose capture and replay parser both pass.
4. Fresh independently signed off-host decrypt/manifest/restore proof and
   provider console/rescue proof, both bound to the pinned verifier and bundle.
5. An observed automatic dead-man rollback rehearsal that restores exact
   service enabled/active state, files, sysctls, modules, routes, firewall,
   listeners, Docker inventory, and production health, followed by byte equality.
6. Post-start acceptance for private API bind, exact CIDRs, bundled containerd,
   NodeAllocatable reservations, port 80/443 ownership, exact live inventory,
   trusted hello, and unchanged production health through a 900-second soak.

Until these gates are independently accepted, do not run arm, seal, rollback,
smoke apply, or any K3s install command on production. `preflight` is staging
validation only: it does not create, open, lock, or truncate the maintenance
file and does not make `apply` available.

There is deliberately no orchestrator maintenance middleware or lock lifecycle
change in this delivery. A future live-install design must introduce and review
that runtime coordination together with the independently proven worker
quiescence contract; it cannot reuse a locally assumed lock prerequisite.

## Local verification

Windows (Git Bash):

```powershell
& 'C:\Program Files\Git\bin\bash.exe' infra/project-cell/tests/test-phase2-tools.sh
```

Linux:

```bash
bash infra/project-cell/tests/test-phase2-tools.sh
PYTHONDONTWRITEBYTECODE=1 python3 infra/project-cell/phase2ctl.py self-check
bash -n infra/project-cell/*.sh infra/project-cell/tests/*.sh
```

The Windows harness explicitly relaxes POSIX ownership assertions because NTFS
does not expose Linux ownership/modes through Python. Production paths enable
strict root uid/gid/mode/type checks. All platforms execute the real hash,
signature, stale/self/loopback/symlink and schema validators, plus zero-dependency
hard-disable sentinels. They do not execute systemd, rollback, install, or
Kubernetes mutation behavior.
