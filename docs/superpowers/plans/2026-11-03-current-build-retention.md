# Current build retention implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for the primary implementation. Independent registry policy and registry maintenance modules may use superpowers:dispatching-parallel-agents with exclusive file ownership. Steps use checkbox syntax for tracking.

**Goal:** Automatically remove obsolete build archives and application images while protecting the current editor/publication, persistent data and unresolved operations.

**Architecture:** Reuse the existing conservative archive collector and workspace locks. Select checkpoint roots from validated current lifecycle state; unknown or unresolved state retains every checkpoint. Inventory the same protected image roots for local Docker cleanup and a registry coordinator. Registry collection preserves reads, temporarily fences public writes, deletes only approved application manifests through a private loopback registry process, stops that private writer, then runs Distribution GC before restoring normal service.

**Tech stack:** Python 3.12, existing Docker/Kubernetes/httpx clients, pytest, systemd, Distribution registry:2. No new application dependencies.

**Spec:** `docs/superpowers/specs/2026-11-03-current-build-retention-design.md` (owner approved automation after clarification excluding new rollback features).

## Global constraints

- Preserve source Git/MinIO history, accounts, billing, uploads, persistent business data and all backups.
- Preserve current editor/accepted state, current publication, and every unresolved operation/recovery dependency.
- Keep at least 7200 seconds of grace for fresh objects; archive/local image sweep every 15 minutes.
- Never run global Docker prune, delete unknown ownership, follow symlinks, or treat unreadable state as empty.
- No new historical-version rebuilding or rollback product feature; existing internal recovery remains protected.
- Registry work must cover all configured controllers and Kubernetes workloads; missing/changed inventories abort deletion.
- Registry GC never overlaps a writable public registry; shared platform manifests remain intact.
- Native execution in the existing owned feature branch; tests first, independent whole-branch review, push and documented production delivery, verified service/HTTP health.

## Review focus

1. A paused app must still start from its current checkpoint after older captures are removed (Task 1).
2. Unknown operation state, ambiguous identities, or a long capture must retain its materials (Tasks 1–2).
3. Multiple tags for one digest and an old image newly pushed under a new tag must remain safe (Task 3).
4. A registry rollout/crash must leave pulls available and must not leave an unowned writer during GC (Task 3).
5. A concurrent change of file, Docker consumer, peer inventory or Deployment UID must stop the affected removal (all tasks).

## Files and ownership

- Primary: checkpoint root policy, existing archive collector, Docker collector/roots API, orchestration scripts, systemd installers, integration and delivery evidence.
- Registry policy implementer: only `services/registry_retention.py` and `tests/test_registry_retention.py`.
- Registry maintenance implementer: only `services/registry_maintenance.py` and `tests/test_registry_maintenance.py`.
- Git commits, CI, production configuration and deployment remain serial under the primary agent.

## Task 1: Current checkpoint roots and safe archive sweep

**Files:** create `apps/orchestrator/src/yleum_orchestrator/services/build_retention.py`; modify `apps/orchestrator/scripts/cleanup_orphan_archives.py`; extend `apps/orchestrator/tests/test_cleanup_orphan_archives.py`; create `apps/orchestrator/tests/test_build_retention.py`.

**Interfaces:** `CheckpointSelection(volume: str, workspace_id: str, refs: frozenset[str] | None, optional_missing: bool)`; `checkpoint_selection(root: Path, path: Path, payload: object, deletion_marker: object | None) -> CheckpointSelection | None`. `refs=None` means preserve all retained envelopes. The existing two-argument `checkpoint_references(declared, optional)` compatibility entry point remains conservative; internal inventory may accept an additional selection map.

- [ ] Write and run failing tests: two completed pauses retain the newest checkpoint and current host environment, allowing only older environment archives to expire; data dump/source files in checkpoint volumes remain untouched.
- [ ] Add RED cases: active/indeterminate/degraded/unknown state retains all; missing latest envelope aborts; mismatched workspace/owner/schema and malformed refs abort or conservatively retain; latest completed restore/current explicit target remains protected.
- [ ] Add RED cases: only a validated deletion tombstone plus completed final destroy can make its missing checkpoint volume optional. Existing missing-retained-volume regression still aborts.
- [ ] Implement pure selection from strict `CellWorkspaceState`, current/latest successful checkpoint refs and unresolved operations, without deleting checkpoint volume contents.
- [ ] Refactor collector inventory to use selected envelope roots; preserve filesystem checks, full post-lock recheck, minimum age and legacy fallback. Include protected image IDs/repository refs from the same selected JSON in an inventory object.
- [ ] Run focused tests, ruff and mypy. Run real Linux lock/symlink tests in CI or an isolated Linux fixture. Expected: all pass; current checkpoint remains importable after sweep.
- [ ] Commit this tested behavior with a focused message.

## Task 2: Local images, protected roots endpoint and automatic host sweep

**Files:** create `services/local_image_retention.py`, `routers/build_retention.py`, `scripts/cleanup_builds.py`; modify `main.py` and `infra/max-k3s/cells/55-archive-retention.sh`; add corresponding tests.

**Interfaces:** protected inventory contains `archive_names`, `image_ids`, `image_refs`, `active_operations`, `observed_at`, and a stable metadata generation/fingerprint. `GET /internal/build-retention/roots` uses existing internal-token verification and returns no client payloads/secrets. CLI defaults to dry run and rejects grace below 7200 seconds.

- [ ] Write/run RED tests: collect image roots from current host/checkpoint state; a full metadata generation changing during scan aborts rather than publishing an incomplete inventory; internal endpoint rejects unauthenticated requests.
- [ ] Write/run RED tests: only managed environment images or strict `registry.yleum.ru/max-app/<uuid>` images are candidates; shared/platform tags, any container consumer, current metadata reference, fresh image or unknown identity preserves the image.
- [ ] Implement addressed `docker image remove` without force under the matching workspace lock, with a fresh reference/consumer/identity check; Docker refusal preserves the image and is recorded.
- [ ] Compose archive and local-image sweep into the systemd entry point; archive failure prevents the image phase. Reuse state-owner identity, nice/IO settings and the fifteen-minute timer.
- [ ] Run existing archive/lock/machine environment/publication tests plus the new endpoint/collector tests. Expected: all pass and no source/data volume deletion API exists in the new collector.
- [ ] Commit. Keep deployment/install serial until integrated review.

## Task 3: Application registry retention with protected current workloads

**Files:** registry implementers own the four files above; primary creates `scripts/cleanup_registry.py`, `infra/max-k3s/cells/56-registry-retention.sh` and coordinator tests/runbook.

**Policy interface:** `RegistryRetention(base_url: str, username: str, password: str, seen_path: Path, *, client: httpx.Client | None = None)` exposes `plan(protected: set[str], now: datetime) -> RegistryPlan` and `apply(plan: RegistryPlan, protected: set[str], *, delete: Callable[[str, str], int]) -> dict[str, int]`. Candidate fields include repository, manifest digest, config digest and all tags. Password fields must never appear in repr/errors/reports. Actual deletion transport is injected by the coordinator; the policy never enables public writable access itself.

**Maintenance interface:** `RegistryMaintenance(kubeconfig_path: str, journal_path: Path, *, namespace: str = 'registry', deployment: str = 'registry', ...)` is a context manager. Its lease provides `assert_read_only()`, `start_private_writer()`, `delete_manifest(repository: str, digest: str, authorization: str) -> int`, `stop_private_writer()`, `garbage_collect() -> dict[str, int]`, and `restore()`. The Kubernetes adapter is injectable for tests. Standalone recovery restores only a Deployment still bearing this job's owner annotation and UID.

- [ ] Policy implementer: RED tests for protected tag/digest/config IDs, aliases, reused old config with a new tag, first-seen grace of 7200 seconds, unsupported indexes, incomplete pagination/HTTP failures, redirected credentials, changed tags and non-application namespaces.
- [ ] Policy implementation: enumerate only strict `max-app/<uuid>` repositories, group all aliases by immutable manifest digest, preserve unknown formats, atomically record first observation of each tag/digest outside live state. Recheck every alias and protected set before calling injected deletion.
- [ ] Maintenance implementer: RED tests for Deployment UID/resourceVersion/owner races, Recreate strategy, old writer pods, readiness, failure/exception/crash recovery, private writer lifetime and GC prohibition while any writer remains.
- [ ] Maintenance implementation: validate registry:2 filesystem/PVC configuration; persist original read-only value and owner intent before patch. Restart registry with read-only HTTP while preserving reads. Require the single new ready read-only pod and absence of old writable pods.
- [ ] Maintenance implementation: start a second registry process bound strictly to `127.0.0.1:5001`, retaining existing authentication and storage configuration. Send only validated manifest DELETE requests through `nc` on pod exec stdin, keeping Authorization out of argv/logs. Stop/verify this private writer before `/bin/registry garbage-collect /etc/docker/registry/config.yml`. Never use `--delete-untagged` or global filesystem deletion. Finally restore only this job's read-only change, preserving concurrent unrelated config.
- [ ] Primary coordinator: load complete current roots from local/peer controllers and Kubernetes workload specs; fail closed on unreachable/stale/malformed/busy inventory. Recheck after entering read-only mode, before manifest deletion and GC. Persist receipts and actual allocated registry bytes before/after.
- [ ] Primary coordinator: add bounded retry for registry-maintenance push failures, preserving existing publication/admission behavior and current app. Registry sweep runs nightly at 04:37 Moscow, offset from the local quarter-hour sweep, with a recovery ExecStopPost and startup recovery. Host sweeps continue every fifteen minutes.
- [ ] Run all focused suites and real isolated registry acceptance: seed current/obsolete manifests, expire only the test observation grace, verify current pulls before/during/after maintenance and old references removed, shared layers/current manifests preserved, no remaining private writer, read-write service restored after injected failure.
- [ ] Commit integrated task. No production data or real app generations are used for fixtures.

## Task 4: Independent review and production acceptance

- [ ] Fresh `gpt-6.1-sol` / `max` reviewer reads spec, plan, complete diff and evidence; fix material findings with RED→GREEN regressions.
- [ ] Run orchestrator suite, ruff, mypy and relevant release/installer checks; inspect full results, not only summaries. Record exact revisions.
- [ ] Push feature branch, obtain green CI for its SHA; integrate to main without rewriting history and deploy using `infra/release/deploy-prod.sh <sha> --no-web` from the production checkout/path.
- [ ] Before enabling deletion, refresh project/operation inventories and validate backups. Reconcile only frozen, already deleted test-project metadata left by the approved one-time purge, archiving exact owned paths outside active state; preserve unknown/new projects.
- [ ] Dry-run on both Core and Commerce, compare protected roots/checksums, install/enable host timers. Install registry coordinator only on Core with all controller URLs/credentials, existing kubeconfig and state-owner permissions. Adding a controller requires updating coverage before admitting that host.
- [ ] Run isolated live acceptance with temporary app-independent fixtures; remove those exact fixture resources afterward. Check timer/service exit codes, no active writable registry helper, source/data preservation and public API/web health. Report only verified completed phases and precise unresolved blockers.

## Plan decisions

- Keep the existing suitable feature checkout/branch; no new worktree or product UI work.
- Reuse file locks and conservative fallback rather than adding a new database/catalog service.
- Registry maintenance is nightly, to limit write interruptions; published app reads remain available. Distribution requires read-only/stopped writers during physical GC (official Distribution garbage collection documentation).
- All operational scripts run dry first. Existing authorization covers the implementation, push/deploy and deletion of obsolete build materials; workflow choices do not require another approval round.
