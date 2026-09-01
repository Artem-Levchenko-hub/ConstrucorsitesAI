# Enterprise Project Cell Phase 2 Isolated K3s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Final delivery boundary (2026-09-01):** This delivery contains read-only
validation and supply-chain scaffolding only. Install apply, rollback, dead-man
arm/disarm, and trusted-hello apply all reject as their first shell action.
There is no `apps/orchestrator` change or maintenance-lock runtime seam in this
diff. Enabling any mutation requires a new plan and independent review.

**Goal:** Build a fail-closed, repeatable Phase 2 foundation that validates all checksum-pinned K3s, trusted-hello, evidence, dead-man, and rollback contracts locally while keeping live installation unavailable until independently observed production gates pass.

**Architecture:** Repository-owned code under `infra/project-cell/` validates
content-addressed evidence, signed recovery attestations, pinned configuration,
and the trusted-hello manifest without changing the host or Kubernetes. Former
mutation implementations are unreachable scaffolding, not an enabled runtime.

**Tech Stack:** Bash 5, Python 3.12 standard library, OpenSSL CMS/signature verification, systemd, K3s `v1.36.4+k3s1`, Kubernetes YAML, shell contract tests.

**Spec:** `docs/superpowers/specs/2026-08-31-enterprise-project-cell-agent-runtime-design.md`

## Global Constraints

- Phase 2 is limited to isolated K3s plus a trusted hello; it installs no Kata runtime, ProjectCell CRD/controller, admission policy, runner, user workload, or production routing.
- K3s is exactly `v1.36.4+k3s1`; amd64 binary SHA-256 is `835873f37245fc615f547a2fe2af9402a347875f13fa64a1f136de644955ea3f` and the official `sha256sum-amd64.txt` SHA-256 is `db1dbdc92f0cb5ccd361348a113f4dff82b1f9194175e5993efc37224a04ba4d`.
- The pinned upstream `install.sh` SHA-256 is `46177d4c99440b4c0311b67233823a8e8a2fc09693f6c89af1a7161e152fbfad`; no channel or `latest` lookup is allowed.
- Trusted hello uses `registry.k8s.io/e2e-test-images/agnhost:2.53@sha256:99c6b4bb4a1e1df3f0b3752168c89358794d02258ebebc26bf21c29399011a85`.
- Pod CIDR is exactly `10.42.0.0/16`; Service CIDR is exactly `10.43.0.0/16`; install fails if either overlaps a host route, Docker network, private administration network, or each other.
- Traefik and ServiceLB are disabled. K3s may not bind its API to the public address, occupy production ports 80/443, or replace system nginx.
- Kubelet reservations protect the legacy Docker stack: `systemReserved` is `cpu=1500m,memory=4Gi,ephemeral-storage=10Gi,pid=4096`; `kubeReserved` is `cpu=500m,memory=1Gi,ephemeral-storage=2Gi,pid=1000`; hard eviction starts at `memory.available=1Gi`, `nodefs.available=15%`, `nodefs.inodesFree=10%`, `imagefs.available=15%`, and `pid.available=10%`; per-pod PID limit is 4096.
- Before host mutation, available memory is at least 6 GiB, free root-disk space is at least 60 GiB, swap use is zero, production health is green, active generation/backup/restore/build/deploy/delete/promotion work is absent, and the global maintenance lock is held.
- The rollback bundle is root-owned, mode `0700`; its manifest and evidence are non-writable, content-addressed, encrypted, copied off-host, checksum-verified there, restore-tested independently, and accepted only with a detached signature from a configured verifier key.
- Provider console/rescue access is independently tested and signed before the dead-man timer can arm.
- The dead-man rollback is installed under `/usr/local/libexec/omnia-project-cell/` and `/var/lib/omnia-project-cell-phase2/`, outside the repository. It stays armed through all network mutation and the production-health soak and disarms only on explicit command with matching fresh postflight evidence.
- Rollback runs the exact K3s cleanup, restores recorded configuration/firewall/routes/DNS/service state, and byte-compares normalized pre/post state. `k3s-uninstall.sh` alone never constitutes rollback proof.
- Existing runtime workspaces, databases, Docker volumes, Docker networks, images, containers, nginx routes, and dirty production files are never removed or rewritten.
- The report stays `testing` with `score: null` until a live server install, hello, production soak, dead-man rehearsal, rollback, and byte comparison have actually passed.
- Every host/Kubernetes mutation entrypoint remains an unconditional hard
  failure. No checklist item below authorizes removing those guards merely
  because read-only contract tests pass.

## Review hardening decision

The security reviews were handled with adversarial tests first. The retained
read-only contract pins verifier/certificate fingerprints, re-verifies canonical
signed source bytes, rejects local/loopback/self/stale attestations and private
keys, normalizes complete restore metadata, and aborts on mandatory capture or
schema failures. It does not claim dead-man, rollback, installer, orchestrator
locking, or Kubernetes apply behavior.

This closes the locally testable findings without claiming a production-safe
mutation path. The remaining production gates are explicitly external: a fresh
signed backup, authoritative reconciliation of every worker/background queue,
live exclusive-lock quiescence, provider firewall/rescue validation, and an
observed rollback/byte-compare rehearsal. Until a separate reviewed change
provides those facts, Phase 2 produces staging evidence only.

The final simplification removed the entire orchestrator seam and hard-disabled
install, rollback, dead-man arm/disarm, and trusted-hello apply before dependency
execution. Shadowed `PATH`/`PYTHONPATH` sentinels prove zero dependency calls and
zero state-directory writes. Read-only preflight never opens a maintenance lock.

## Primary-source decisions

- K3s server flags and component names: https://docs.k3s.io/cli/server
- K3s config file, YAML mapping, and kubelet drop-ins: https://docs.k3s.io/installation/configuration
- K3s network ports and the warning not to expose VXLAN publicly: https://docs.k3s.io/installation/requirements#networking
- K3s release and assets: https://github.com/k3s-io/k3s/releases/tag/v1.36.4%2Bk3s1
- Pinned upstream installer: https://github.com/k3s-io/k3s/blob/v1.36.4%2Bk3s1/install.sh
- Kubernetes node reservations and eviction semantics: https://kubernetes.io/docs/tasks/administer-cluster/reserve-compute-resources/
- Kubernetes official `agnhost` image purpose: https://github.com/kubernetes/kubernetes/blob/master/test/images/README.md
- `agnhost` `/echo` port-forward behavior: https://github.com/kubernetes/kubernetes/blob/master/test/e2e/kubectl/kubectl.go
- Kata `4.1.0` is evidence for the next phase only: https://github.com/kata-containers/kata-containers/releases/tag/4.1.0

---

### Task 1: Freeze the Phase 2 contract with failing tests

**Files:**
- Create: `infra/project-cell/tests/test-phase2-tools.sh`
- Create: `infra/project-cell/tests/fixtures/`

**Interfaces:**
- Consumes: the approved spec and Global Constraints above.
- Produces: one executable shell contract test invoked as `bash infra/project-cell/tests/test-phase2-tools.sh`.

- [ ] **Step 1: Write failing tests for immutable evidence and bundle verification**

  Create fixtures with deterministic routes, firewall saves, DNS bytes, service state, Docker network JSON, health payloads, and exact file-presence metadata. The test must name these mutations: changing one evidence byte, making the manifest group-readable, using a symlink as a bundle/receipt/key, omitting a required evidence class, or adding an unmanifested restore file must make verification fail.

- [ ] **Step 2: Write failing tests for signed off-host and rescue attestations**

  Generate ephemeral RSA keys in the test directory. Require valid detached signatures, matching bundle/ciphertext checksum, `location` with a non-local scheme, a verifier hostname different from the production hostname, `restore_test: passed`, and a recent timestamp. Require a separately signed `provider_rescue` attestation with `console_login: passed` and `rescue_boot: passed`. Wrong checksum, local paths, self-verification, stale timestamps, and unsigned JSON must fail.

- [ ] **Step 3: Write failing tests for K3s rendering and install gates**

  Assert that rendering accepts only a host-present RFC1918 address inside an explicitly approved administration CIDR, produces fixed pod/service CIDRs, disables `traefik` and `servicelb`, keeps kubeconfig `0600`, enables secrets encryption, and installs the exact kubelet reservation drop-in. Public/loopback/unknown bind addresses, CIDR overlap, missing reservations, insufficient memory/disk, swap use, unhealthy production, active work, an unlocked maintenance file, an unarmed dead-man, or an unexpected installed K3s version must fail before the fake installer marker is touched.

- [ ] **Step 4: Write failing tests for dead-man state transitions**

  Use a fake `systemctl` and a fake protected root. `arm` must require verified bundle/off-host/rescue markers, install tools outside the checkout with modes `0700`/`0600`, write an exact bundle pointer, and activate the timer. `disarm` must fail without a matching postflight record containing a completed soak of at least 900 seconds and successful production, hello, and rollback-rehearsal results.

- [ ] **Step 5: Write failing tests for trusted hello and byte comparison**

  Use a fake `kubectl` to require only namespace, Deployment, and ClusterIP Service from the trusted manifest, the exact digest-pinned image, `automountServiceAccountToken: false`, non-root/read-only/no-capability security settings, resource requests/limits, and a localhost port-forward whose `/echo?msg=trusted-hello` body is exact. A NodePort/LoadBalancer/Ingress, an extra namespace workload, a tag-only image, or a changed normalized rollback snapshot must fail.

- [ ] **Step 6: Run the test and record RED**

  Run: `bash infra/project-cell/tests/test-phase2-tools.sh`

  Expected: FAIL because `phase2ctl.py`, capture/seal/install/dead-man/rollback/smoke scripts, pinned config, systemd units, and trusted manifest do not yet exist. Record the command, expected missing artifact, and exit code in the final report and `otchet/data.json` evidence.

---

### Task 2: Implement evidence, manifest, encryption, and independent verification

**Files:**
- Create: `infra/project-cell/phase2ctl.py`
- Create: `infra/project-cell/capture-host-evidence.sh`
- Create: `infra/project-cell/seal-rollback-bundle.sh`
- Create: `infra/project-cell/versions.env`

**Interfaces:**
- Consumes: a protected bundle directory and deterministic host command outputs.
- Produces: `manifest.json`, `bundle.tar`, `bundle.tar.cms`, `bundle.tar.cms.sha256`, `offhost.verified.json`, and `rescue.verified.json`.
- CLI: `phase2ctl.py manifest|verify-bundle|verify-attestation|render-config|snapshot|compare`.

- [ ] **Step 1: Implement pinned supply-chain metadata**

  Store only the exact K3s version, checksum-file digest, binary digest, installer digest/URL, official hello image tag+digest, and the Phase 3 Kata evidence. The install path must refuse missing values, uppercase/non-hex checksums, version/channel substitutions, and any image without `@sha256:`.

- [ ] **Step 2: Implement deterministic host capture**

  Capture package versions; K3s/Kata/containerd/Docker presence; selected systemd unit files and active/enabled state; sysctls/modules; iptables/ip6tables/nft state or explicit absence markers; normalized routes; exact `/etc/resolv.conf` type/target/bytes; CNI inventory; normalized Docker networks/containers/volumes; listeners; memory/disk/inodes/swap; production Git/health; active-operation evidence; backup metadata; and exact before-state presence for K3s paths. Never read `.env`, Kubernetes tokens, registry credentials, or container environment values.

- [ ] **Step 3: Implement a content-addressed permission gate**

  Sort manifest entries by relative POSIX path; include SHA-256, byte length, file type, owner, group, and mode; reject symlinks except explicitly recorded filesystem-state links; reject missing/extra files; require bundle directory `0700`, protected metadata `0600` while assembling, and final evidence/manifest non-writable. Verification recomputes every digest instead of trusting the manifest.

- [ ] **Step 4: Implement encryption and checksum generation**

  Create a deterministic tar from the verified bundle, encrypt it using `openssl cms -encrypt -binary -aes-256-cbc` and an operator-supplied recipient certificate, write SHA-256 beside the ciphertext, and refuse to overwrite an existing output or accept a private key as a recipient. Remove temporary plaintext archives on every exit; leave the source evidence protected for rollback.

- [ ] **Step 5: Implement signed independent attestation verification**

  Verify detached signatures with an operator-supplied public key before parsing JSON. Bind the receipt to bundle id, ciphertext SHA-256, production hostname, a different verifier hostname, non-local off-host location, verification time, checksum pass, decrypt pass, manifest pass, and restore-test pass. Bind rescue proof to the same host and require provider console login and rescue boot pass. Write only content-addressed verified markers.

- [ ] **Step 6: Run the focused test to GREEN**

  Run: `bash infra/project-cell/tests/test-phase2-tools.sh evidence`

  Expected: PASS for valid fixtures and explicit failure for every byte/permission/signature mutation.

---

### Task 3: Implement pinned K3s configuration and fail-closed installation

**Files:**
- Create: `infra/project-cell/config/k3s-config.yaml.template`
- Create: `infra/project-cell/config/10-project-cell-reserves.conf`
- Create: `infra/project-cell/install-isolated-k3s.sh`

**Final status:** configuration validation is retained; installation and the
originally proposed orchestrator middleware are not delivered.

**Interfaces:**
- Consumes: verified bundle markers, signed rescue/off-host markers, private bind address/admin CIDR, expected Git revision, and the global maintenance lock.
- Produces: `/etc/rancher/k3s/config.yaml`, `/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-project-cell-reserves.conf`, installed exact K3s binary/service, and a post-install evidence snapshot.

- [ ] **Step 1: Render the fixed server configuration**

  Render `bind-address`, `advertise-address`, `node-ip`, and `tls-san` from one verified private address; hardcode the two non-overlapping CIDRs; disable only `traefik` and `servicelb`; set kubeconfig mode `0600`; enable secrets encryption; retain K3s bundled containerd and local-path storage; and never set Docker as the CRI.

- [ ] **Step 2: Install the kubelet reservation drop-in before first start**

  Use `apiVersion: kubelet.config.k8s.io/v1beta1`, `kind: KubeletConfiguration`, the exact reservation/eviction/PID values from Global Constraints, and `enforceNodeAllocatable: [pods]`. Do not enforce system cgroups until Phase 3 has profiled and created explicit safe cgroups.

- [ ] **Step 3: Implement pre-mutation gates**

  Require root, amd64, clean/exact server revision, verified bundle, signed off-host/rescue markers, fresh baseline health/backup/operation evidence, capacity thresholds, private bind address present on the host, no CIDR/listener conflict, restorable firewall tooling, an active dead-man timer, and exclusive non-blocking `flock` on `/opt/omnia-runtime/maintenance.lock`. The installer command must remain untouched until every check passes.

  The proposed orchestrator middleware is deferred to a future reviewed live
  mutation plan and is absent from this diff.

- [ ] **Step 4: Download and verify exact upstream artifacts**

  Download the pinned `sha256sum-amd64.txt` and `install.sh` to a protected temporary directory, verify both recorded digests and the exact `k3s` line, then run the installer with `INSTALL_K3S_VERSION=v1.36.4+k3s1`, `INSTALL_K3S_SKIP_START=true`, and `INSTALL_K3S_SKIP_ENABLE=true`. Any redirect to another tag, checksum mismatch, or version mismatch aborts.

- [ ] **Step 5: Start K3s only while the dead-man and lock remain live**

  Enable/start the exact unit, wait for `Ready`, confirm exact server version, private API listener, bundled containerd, expected CIDRs, kubelet `NodeAllocatable` reduction, absence of Traefik/ServiceLB, absence of port 80/443 listeners owned by K3s, and unchanged production health. Leave the dead-man armed.

- [ ] **Step 6: Run the focused test to GREEN**

  Run: `bash infra/project-cell/tests/test-phase2-tools.sh install`

  Expected: PASS for validation gates and the zero-dependency hard-disable sentinel.

---

### Task 4: Implement host-local dead-man and rollback byte proof

**Final status:** not delivered; arm, disarm, and rollback are hard-disabled.

**Files:**
- Create: `infra/project-cell/deadman-control.sh`
- Create: `infra/project-cell/rollback-phase2.sh`
- Create: `infra/project-cell/systemd/omnia-project-cell-deadman.service`
- Create: `infra/project-cell/systemd/omnia-project-cell-deadman.timer`

**Interfaces:**
- Consumes: a verified bundle outside the checkout and a matching postflight record.
- Produces: protected `armed.json`, systemd timer state, `rollback-result.json`, and byte-comparison evidence.

- [ ] **Step 1: Install protected rollback tooling outside Git**

  Copy exact reviewed scripts and units to root-owned locations with no group/world permissions, copy the verified public metadata needed to validate the bundle, write an absolute non-symlink bundle pointer, daemon-reload, and verify the installed script hashes match repository artifacts.

- [ ] **Step 2: Arm an idempotent persistent timer**

  Require signed rescue/off-host markers and verified bundle, write `armed.json` atomically, start a persistent timer with a 20-minute deadline, and verify `systemctl is-active`. Re-arming the same bundle refreshes the deadline; a different bundle while armed fails.

- [ ] **Step 3: Implement exact rollback actions**

  Stop/disable K3s, run the exact generated killall/uninstall helpers if and only if they match the installed Phase 2 state, restore protected pre-existing files/symlinks and firewall saves, replay the recorded route save, remove only exact Phase 2 paths recorded absent before installation, daemon-reload, restore original service enabled/active states, restart Docker only when its recorded config changed, and recheck production health. Never remove a Docker object or existing runtime path.

- [ ] **Step 4: Implement byte-comparison acceptance**

  Re-capture deterministic firewall, routes, DNS, service state, container-runtime config, Docker network inventory, K3s-path absence/presence, and production health into a normalized snapshot. `phase2ctl.py compare` must list every changed byte/path and fail until all rollback-contract entries match the baseline.

- [ ] **Step 5: Require explicit evidence before disarm**

  A matching postflight record must report exact bundle id, exact installed version, trusted hello pass, unchanged production health, soak duration at least 900 seconds, dead-man rehearsal pass, and rollback byte comparison pass. Disarm stops/disables the timer and removes only `armed.json`; it retains bundle/result evidence.

- [ ] **Step 6: Run the focused test to GREEN**

  Run: `bash infra/project-cell/tests/test-phase2-tools.sh deadman`

  Expected: PASS for idempotent arm, automatic trigger, matching rollback, and explicit disarm; fail for stale/mismatched/short-soak evidence.

---

### Task 5: Implement digest-pinned trusted hello and operator runbook

**Final status:** manifest validation only; Kubernetes apply is hard-disabled.

**Files:**
- Create: `infra/project-cell/manifests/trusted-hello.yaml`
- Create: `infra/project-cell/smoke-trusted-hello.sh`
- Create: `infra/project-cell/README.md`

**Interfaces:**
- Consumes: ready exact K3s and the trusted manifest.
- Produces: `trusted-hello-result.json` and the human execution/recovery contract.

- [ ] **Step 1: Define the smallest trusted workload**

  Use namespace `omnia-project-cell-system`, one-replica Deployment, and ClusterIP Service. The pod runs `/agnhost netexec --http-port=8080`, disables service-account token mounting, runs non-root with read-only root filesystem, drops all capabilities, enables seccomp RuntimeDefault and no privilege escalation, and has fixed CPU/memory/ephemeral-storage requests and limits. Do not define Ingress, NodePort, LoadBalancer, PVC, host namespace, hostPath, or RuntimeClass.

- [ ] **Step 2: Implement the smoke**

  Verify the manifest structurally before apply; apply server-side, wait for rollout, require the exact imageID digest, require that only the allowlisted hello resources exist outside K3s system namespaces, port-forward the ClusterIP Service to `127.0.0.1`, and require the exact `trusted-hello` echo. Record node/version/resource/health evidence without secrets and leave the workload running only through the soak.

- [ ] **Step 3: Document the exact execution gates and commands**

  Document capture, independent encryption/upload/restore/signing, rescue attestation, arm, dry-run gate, install, hello, 15-minute production soak, rollback rehearsal, byte comparison, explicit disarm, and retained evidence. Clearly label all server commands as not yet executed and list Kata `4.1.0` only as the next Phase 3 gate.

- [ ] **Step 4: Run all Phase 2 tests to GREEN**

  Run: `bash infra/project-cell/tests/test-phase2-tools.sh`

  Expected: `phase2 contract tests passed` with zero warnings/errors.

---

### Task 6: Record honest partial progress and verify the repository diff

**Files:**
- Modify: `otchet/data.json`

**Interfaces:**
- Consumes: local RED/GREEN command evidence and the still-unexecuted server gates.
- Produces: updated H127/V4 progress visible in `/otchet` without claiming live K3s success.

- [ ] **Step 1: Update the report**

  Set `meta.updated` to `2026-09-01` and increment `meta.version`. Add an `owner_actions` entry for the approved Phase 2 start. Update H127 to `status: testing`, `score: null`, append the exact local RED/GREEN evidence and server audit, and state that K3s/Kata are not installed. Add a false V4 step for the Phase 2 live install/hello/rollback proof; do not mark the existing final Project Cell/MAX step complete.

- [ ] **Step 2: Validate report invariants**

  Run: `python -m json.tool otchet/data.json > /dev/null`

  Run a Python assertion that every vector `done` count equals the number of true `steps`, H127 appears in V4, and H127 has `score is None` while not live-proven.

- [ ] **Step 3: Run static and syntax checks**

  Run: `python -m compileall -q infra/project-cell`

  Run: `bash -n infra/project-cell/*.sh infra/project-cell/tests/*.sh`

  Run: `python infra/project-cell/phase2ctl.py self-check`

  Run ShellCheck if installed; if unavailable, record that fact and rely on `bash -n` plus the behavior suite.

- [ ] **Step 4: Run the complete local verification fresh**

  Run: `bash infra/project-cell/tests/test-phase2-tools.sh`

  Run: `python -m json.tool otchet/data.json > /dev/null`

  Run: `git diff --check`

  Run: `git status --short` and `git diff --stat`; verify no file outside `infra/project-cell/**`, this plan, and `otchet/data.json` changed.

- [ ] **Step 5: Stop before delivery or production mutation**

  Do not install K3s, arm production systemd, deploy, commit, or push in this task. Hand the reviewed diff and the unresolved live gates to the separate delivery/server-execution step.
