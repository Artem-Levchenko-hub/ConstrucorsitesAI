# Project database role upgrade quiesce transport plan

> For agentic workers: use executing-plans for implementation and verification-before-completion for delivery claims.

**Goal:** unblock the already-delivered project database role upgrade without changing business data or accepted application release.

**Architecture:** replace full Deployment GET replay through server-side apply with a replicas-only JSON merge patch and an optional resourceVersion precondition. Retain the existing observed-generation and complete guest-death wait gate.

**Tech stack:** Python, Kubernetes dynamic client, pytest, production canonical deployment.

**Spec:** T11.4 scoped Coffee live reproduction on 15f5e4fb; actual API-server dry-run rejects full GET apply with HTTP400 and accepts a replicas-only merge patch while the physical object stays unchanged.

## Global constraints

Preserve application source/image, PVC identity, accepted release and pre-upgrade business-row hashes. No new generation, publication, provider record or manual role upgrade. Keep Git and production deployment serial. A code deployment does not itself close T11.4.

## Review focus

No server-owned GET metadata replay; resourceVersion conflict propagates without force/retry. No schema/role work before guest pods disappear. Physical runtime SQL denial, role flags and unchanged business data are required after delivery.

## Task 1: bounded transport fix and delivery

- [x] Preserve actual failing live checkpoint and paired non-mutating dry-run.
- [x] Add eight transport/wait/conflict regression cases; observe seven fail on old source, one pre-existing safe absent-deployment branch passes.
- [x] Implement scale_deployment protocol and replicas-only merge patch; adapt existing placement fake.
- [x] Independent source review and focused role/publication regression checks, lint and types.
- [x] Update dated tracked ledger with scoped second-real-MAX-actor CRM evidence; exclude invalid crop images.
- [ ] Commit/push and require exact-revision full CI success.
- [ ] Canonical production deployment with backup, idle/fence and health receipts.
- [ ] Read actual Coffee role/identity/data proof and execute bounded runtime SQL permission-denial checks; report blockers honestly.
