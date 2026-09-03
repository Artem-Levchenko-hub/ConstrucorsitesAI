# Docker Project Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. One source owner executes inline; parent performs independent review; delivery agent alone commits, pushes and deploys.

**Goal:** Deliver a Docker owner-canary project machine that can use arbitrary Linux userland stacks to create, verify, retain and publish a real MAX mini-app.

**Architecture:** Extend the existing fenced Docker Project Cell with portable manifests, persistent environment artifacts and supervised services. Keep trusted runner, generated execution, platform identity and release/data coordination separate. Retain the old MAX adapter until the new path passes its full deterministic acceptance matrix.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy/PostgreSQL, Docker Engine SDK, Linux processes, existing Next.js MAX boundary, existing HTTP/WS gateway; generated application technologies are not enumerated.

**Spec:** `docs/superpowers/specs/2026-09-03-docker-project-machine-design.md`

## Global Constraints

- Retain the existing authenticated, verified, allowlisted owner-canary rollout boundary. Do not enable arbitrary public accounts.
- Use Docker now; do not install K3s/Kata or weaken their existing installation guards.
- No generated workload receives privileged mode, host namespaces, devices, sockets, host mounts, platform database credentials, runner credentials, or control-plane authority.
- Docker uses a shared host kernel. This owner canary is not represented as microVM isolation or a qualified hostile multi-tenant service.
- No framework, language, package-name, package-registry, table-count, file-count, or service-count allowlist. Linux userland compatibility, available resources, MAX compatibility and isolation are the boundaries.
- Public dependency/application egress is permitted only through externally enforced destination controls; private/host/platform/metadata/cross-project destinations remain blocked.
- `see` is removed from every generation path, including native, legacy and MAX: no exposed tool, dispatch, prompt requirement or automatic retry. Manual live preview remains available. Existing build/runtime/auth/data checks remain; no replacement visual gate is introduced.
- No user/model generation runs during implementation verification. Use authored deterministic fixtures.
- Source changes are verified, reviewed and handed to delivery for commit, push, documented production deployment and health confirmation. Existing unrelated work is preserved.

## Execution and evidence

Run orchestrator commands from `apps/orchestrator` with `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on Linux. Run API commands from `apps/api` with its venv; API DB cases require the disposable database supplied by the parent and a dummy test JWT secret. Run runner commands from `apps/agent-runner` with an environment containing its development dependencies. Do not substitute the production database. Every test-creation step is followed by observed RED, minimal code, then observed GREEN. Record concrete results under the matching task before delivery. A missing external test gate is not a skipped pass.

## File map

- `apps/orchestrator/src/omnia_orchestrator/core/project_machine.py`: portable JSON manifest and immutable references.
- `apps/orchestrator/src/omnia_orchestrator/services/project_machine.py`: lifecycle coordination behind a narrow Docker backend.
- `apps/orchestrator/src/omnia_orchestrator/services/machine_environment.py`: immutable environment snapshot/export/import verification.
- `apps/orchestrator/src/omnia_orchestrator/services/machine_services.py`: desired-service graph and process supervision protocol.
- `apps/orchestrator/src/omnia_orchestrator/services/machine_egress.py`: destination policy and fence readiness contract.
- `apps/orchestrator/src/omnia_orchestrator/routers/project_machine.py`: internal fenced machine operations.
- `apps/agent-runner/src/omnia_agent_runner/session.py`: durable session/operation/event adapters.
- `apps/api/src/omnia_api/services/project_machine_sessions.py`: API lease/dispatch/recovery boundary.
- `apps/api/src/omnia_api/services/project_machine_release.py`: functional candidate verification and data cutover coordinator.
- Existing workspace/capacity/candidate/runtime/MAX code: narrow compatibility adapters only.

### Task 0: Remove `see` from all generation (first independent delivery)

User priority and parent review explicitly approve this bounded slice before any machine runtime changes.

**Files:** `apps/api/src/omnia_api/services/{agent_builder,agent_native,max_generation_contract,project_cell_executor}.py`, `apps/api/src/omnia_api/routers/messages.py`, their focused tests and adjacent design-contract documentation.

- [x] Write regressions for removed native/legacy tool schemas and dispatch, stale model replies, no automatic visual retry, and MAX completion without visual evidence.
- [x] Observe RED: 9 expected failures and 1 pass; then remove tool/prompt/dispatch/completion code and observe focused GREEN: 10 passed.
- [ ] Update previous visual-loop tests into functional runtime/probe repair tests without weakening build/runtime/auth/data checks; run focused suites, lint and diff sanity.
- [ ] Parent review, then delivery agent commits/pushes/deploys; confirm production health and exact revision. No real model/user generation is run.
- [ ] Only after this slice is delivered, continue the machine tasks below. This slice is not a working arbitrary-stack machine.

### Task 1: Portable manifest and resource/service contract

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/core/project_machine.py`
- Create: `apps/orchestrator/tests/test_project_machine_manifest.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/workspace_provider.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`

**Interfaces:**
- Consumes: JSON bytes at `.omnia/cell.json` and the existing `CellResourceProfile` resource budget.
- Produces: `MachineManifest.model_validate(payload)`, `MachineManifest.canonical_json() -> str`, `MachineManifest.digest() -> str`, `MachineManifest.service_order() -> tuple[str, ...]`, `MachineManifest.resource_request() -> MachineResources`.
- Types: `MachineResources(cpu_cores, memory_bytes, disk_bytes, pids)`, `MachineTask(name, role, argv, cwd)`, `MachineService(name, argv, cwd, depends_on, mounts, readiness, restart, resources)`, `MachineMount(volume, target)`, `MachineRoute(path, service, port)`, `MachineDataStore(name, volumes, quiesce_task, restore_check_task)`.

- [ ] **Step 1: Write behavior tests before production code.** Reject invalid traversal/mounts, duplicate names/routes, missing dependencies, graph cycles, unreferenced tasks, secret/host-control fields and non-finite resources; accept unlike languages and any package commands.

```python
def test_manifest_does_not_impose_a_framework_allowlist():
    payload = machine_payload(services=[service("api", ["ruby", "server.rb"])])
    manifest = MachineManifest.model_validate(payload)
    assert manifest.service_order() == ("api",)
    assert manifest.services[0].argv == ["ruby", "server.rb"]

def test_resource_request_sums_services():
    manifest = MachineManifest.model_validate(machine_payload())
    assert manifest.resource_request().memory_bytes == sum(
        item.resources.memory_bytes for item in manifest.services
    )
```

- [ ] **Step 2: Run RED:** `python -m pytest tests/test_project_machine_manifest.py -q`. Confirm missing manifest behavior, not malformed test fixtures.
- [ ] **Step 3: Implement strict versioned models and canonical digest.** Use `extra="forbid"`; validate references and topologically order the service graph without invoking any commands. Keep manifest fields language-neutral.

```python
def digest(self) -> str:
    return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run GREEN and compatibility:** `python -m pytest tests/test_project_machine_manifest.py tests/test_workspace_provider.py tests/test_workspace_router.py -q`; run Ruff on changed Python files.
- [ ] **Step 5: Parent review checkpoint.** Record manifest test evidence; do not present this contract-only slice as a working machine or deliver it independently without parent direction.

### Task 2: Persistent machine and immutable environment recovery

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/services/project_machine.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/machine_environment.py`
- Create: `apps/orchestrator/tests/test_machine_environment.py`
- Create: `apps/orchestrator/tests/test_project_machine.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_py_cell_backend.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/cell_state.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/cell_checkpoint.py`

**Interfaces:**
- Consumes: `MachineManifest`, existing `LifecycleMutation`, project-owned volumes and `WorkspaceOperationLock`.
- Produces: `MachineEnvironmentRef(image_id: str, artifact_ref: str, sha256: str, manifest_digest: str)`; `MachineEnvironmentStore.capture(workspace_id, mutation) -> MachineEnvironmentRef`; `restore(workspace_id, reference, mutation) -> None`; `ProjectMachine.ensure(manifest, mutation)`, `exec_start(argv, cwd, mutation) -> operation_id`, `exec_status(operation_id, mutation) -> MachineOperationResult`, `cancel(mutation) -> None`.

- [ ] **Step 1: Write persistence/recovery and stale-owner tests.** The backend test double stores actual archive bytes and simulates removed containers/images; tests assert round-trip file data, digest failure, stale lease rejection, cancellation and no reuse of unverified artifacts.

```python
async def test_restore_rejects_corrupted_environment(machine):
    reference = await machine.environments.capture(machine.id, machine.mutation)
    machine.artifacts.corrupt(reference.artifact_ref)
    with pytest.raises(EnvironmentIntegrityError):
        await machine.environments.restore(machine.id, reference, machine.mutation)
    assert not machine.backend.running_machine_ids()
```

- [ ] **Step 2: Run RED:** `python -m pytest tests/test_machine_environment.py tests/test_project_machine.py -q`.
- [ ] **Step 3: Implement controller-owned rootfs capture/import with image/volume digests.** Stop mutable machine operations before capture; sanitize image configuration; keep transient mounts outside snapshots. Restore image plus repository/dependency/home volumes; never reuse `/app/node_modules` or exclude installed project environments. Command state survives transport loss; cancellation removes the exact fenced container and confirms death.

```python
if hashlib.sha256(artifact_bytes).hexdigest() != reference.sha256:
    raise EnvironmentIntegrityError("environment artifact digest mismatch")
```

- [ ] **Step 4: Run GREEN and existing resource/checkpoint regressions:** `python -m pytest tests/test_machine_environment.py tests/test_project_machine.py tests/test_cell_checkpoint.py tests/test_docker_py_cell_backend.py -q`.
- [ ] **Step 5: Capture first real Linux fixture evidence when test Docker is available:** create a project-owned machine, install a package/system utility, capture, remove/recreate from artifact, and assert exact versions plus dependency import and retained source. No model call or production project.

### Task 3: Public egress and supervised multi-service runtime

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/services/machine_egress.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/machine_services.py`
- Create: `apps/orchestrator/tests/test_machine_egress.py`
- Create: `apps/orchestrator/tests/test_machine_services.py`
- Create: `apps/orchestrator/scripts/project_machine_namespace_guard.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/workspace_provider_factory.py`

**Interfaces:**
- Consumes: `MachineManifest`, controller-owned network identities, `LifecycleMutation`, real namespace-guard observation.
- Produces: `public_destination(address: str) -> bool`, `EgressReadiness(ready: bool, policy_digest: str, reason: str)`, `MachineServices.reconcile(manifest, mutation) -> list[ServiceStatus]`, `status(mutation)`, `stop(mutation)`, `logs(service_name, mutation)`.

- [ ] **Step 1: Write negative destination and process-lifecycle tests.** Exercise loopback/private/link-local/metadata/mapped IPv6, public address acceptance, DNS answers changing to forbidden IP, stale fence, dependency order, readiness failure, process death and restart budget.

```python
@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_private_egress_is_denied(address):
    assert public_destination(address) is False

async def test_services_do_not_start_without_external_fence(machine):
    machine.egress.ready = False
    with pytest.raises(MachineNetworkUnavailable):
        await machine.services.reconcile(machine.manifest, machine.mutation)
    assert machine.backend.running_service_names() == []
```

- [ ] **Step 2: Run RED:** `python -m pytest tests/test_machine_egress.py tests/test_machine_services.py -q`.
- [ ] **Step 3: Implement explicit external-fence readiness, controlled public proxy/resolver and service reconciliation.** Network attachment and route targets derive from trusted project identity. A trusted namespace guard owns only the cell network namespace and its filtering rules; it has no host namespace, socket or host mounts. Generated containers share that namespace without `NET_ADMIN`, `NET_RAW` or `SYS_ADMIN`. Guard readiness emits a policy digest and fails before attachment on identity mismatch. Do not modify Docker-managed host chains, daemon address pools or existing networks. Prove root project workloads cannot bypass the boundary before enabling public egress. Supervisor service IDs include workspace/session epoch; no arbitrary host bind flags.

```python
if not egress.ready:
    raise MachineNetworkUnavailable(egress.reason)
for name in manifest.service_order():
    await backend.ensure_service(manifest.service(name), mutation)
```

- [ ] **Step 4: Run GREEN, resource admission and lock regressions:** `python -m pytest tests/test_machine_egress.py tests/test_machine_services.py tests/test_cell_admission.py tests/test_cell_reservations.py tests/test_cell_lock.py -q`.
- [ ] **Step 5: Real Linux negative probes:** direct Internet bypass, host bridge address, platform ports, another cell DB, alternate DNS and IPv6 denied; two unrelated public registries and system repository installation succeed through the proxy. Verify frontend/backend/worker all start and remain supervised after HTTP transport closes.

Known infrastructure prerequisite observed during isolated test setup: Docker reports predefined address pools fully subnetted when creating another network. No networks or pool settings were changed. Resolve with a separately reviewed, inventory-backed network allocation plan before the three-cell machine acceptance; do not infer host RAM/CPU exhaustion.

### Task 4: Framework-neutral MAX identity and custom project data

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/routers/project_machine.py`
- Create: `apps/orchestrator/tests/test_project_machine_router.py`
- Create: `apps/api/src/omnia_api/services/project_machine_runtime.py`
- Create: `apps/api/tests/test_project_machine_runtime.py`
- Modify: `apps/api/src/omnia_api/services/orchestrator_client.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_executor.py`
- Modify: `apps/api/src/omnia_api/services/max_project_kit.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/cell_draft_support.py`

**Interfaces:**
- Consumes: selected provider capabilities, manifest routes, current signed MAX session and project identity.
- Produces: `ProjectMachineRuntime.execute(action, identity) -> dict`, `preview_session(project_id, owner_id)`, language-neutral reserved MAX paths, and capability-driven agent instructions.

- [ ] **Step 1: Write route/auth tests before replacing source assumptions.** A Python product API receives a verified identity, spoofed headers are removed, wrong-project/expired sessions fail, product SQL reaches only draft DB, and legacy MAX paths remain unchanged.

```python
async def test_product_route_does_not_accept_forged_subject(machine_client):
    response = await machine_client.get("/api/product/history", headers={"X-Omnia-User": "other"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run RED:** API `python -m pytest tests/test_project_machine_runtime.py -q`; orchestrator `python -m pytest tests/test_project_machine_router.py -q`.
- [ ] **Step 3: Implement product-route/managed-core separation and project data identities.** Keep MAX cryptographic validation/integration secrets outside product runtimes. Only the machine adapter bypasses fixed bundled-image dependency and TypeScript path guards after its capability/identity checks pass; the legacy path retains its current behavior.

```python
if selection.machine and selection.capabilities.ready:
    return await machine_runtime.execute(action, identity)
return await legacy_executor(action)
```

- [ ] **Step 4: Run GREEN plus existing MAX/Cell authentication, preview, executor and model-write security regressions.** Record exact selected test files and counts.
- [ ] **Step 5: Real fixture:** JS frontend calls Python backend, authenticated API writes a custom SQL table and reads it after service restart; a second signed user cannot read/mutate the first user's row.

### Task 5: Durable resident runner and session recovery

**Files:**
- Create: `apps/agent-runner/src/omnia_agent_runner/session.py`
- Create: `apps/agent-runner/tests/test_session.py`
- Create: `apps/api/src/omnia_api/services/project_machine_sessions.py`
- Create: `apps/api/tests/test_project_machine_sessions.py`
- Create: `apps/api/migrations/versions/0056_project_machine_sessions.py`
- Modify: `apps/api/src/omnia_api/models/project_cell.py`
- Modify: `apps/agent-runner/src/omnia_agent_runner/service.py`
- Modify: `apps/agent-runner/src/omnia_agent_runner/runner.py`
- Modify: `apps/api/src/omnia_api/services/generation_runs.py`
- Modify: `apps/api/src/omnia_api/main.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`

**Interfaces:**
- Consumes: existing `RunnerIdentity`, `ExecutorClient`, `ControlClient`, `EventSink`, gateway auth factory, machine command operations.
- Produces: `MachineSessionStore.claim`, `renew`, `reserve_operation`, `complete_operation`, `checkpoint`, `cancel`, `recover`, `events_after`; `MachineRunHandler.submit(request)` configured into resident `RunnerService`.

- [ ] **Step 1: Write actual disposable-DB lease/concurrency and runner interruption tests.** Duplicate delivery yields one side effect, sequence resumes after restart, stale/cancel epochs fail, unknown operation outcome stays indeterminate, and epoch transfer waits for old container death.

```python
async def test_cancel_fences_pending_promotion(session_store, active_session):
    cancelled = await session_store.cancel(active_session.id)
    assert cancelled.cancel_epoch == active_session.cancel_epoch + 1
    with pytest.raises(StaleMachineSession):
        await session_store.reserve_operation(active_session, "old-op", "digest")
```

- [ ] **Step 2: Run RED:** runner `python -m pytest tests/test_session.py -q`; API `python -m pytest tests/test_project_machine_sessions.py -q` against disposable DB only.
- [ ] **Step 3: Implement additive session/event/tool-operation/checkpoint persistence and adapters.** Persist complete proposed tool turns before effects; allocate operation ID from persisted turn/tool ID; renew durable leases; use out-of-runner artifact references; replay safe event payloads by sequence. Use short-lived project/session grants, not a shared signing key inside a project container. Keep API loops for legacy only.

```python
operation = await store.reserve_operation(identity, tool_call_id, request_digest)
if operation.completed:
    return await artifacts.read_verified(operation.result_ref)
return await executor.resume_or_start(operation, identity)
```

- [ ] **Step 4: Run GREEN and migration upgrade/downgrade/runner-auth regressions.** Run deterministic pause/cancel/API-restart/runner-restart tests without contacting a model.
- [ ] **Step 5: Confirm the resident service is ready only when authenticated durable adapters are configured; otherwise retain fail-closed 503 behavior.** No unguarded network submission endpoint.

### Task 6: Functional completion without mandatory vision

**Files:**
- Create: `apps/api/tests/test_project_machine_completion.py`
- Modify: `apps/api/src/omnia_api/services/max_generation_contract.py`
- Modify: `apps/api/src/omnia_api/services/agent_native.py`
- Modify: `apps/api/src/omnia_api/services/release_proof.py`
- Modify: `apps/api/src/omnia_api/services/project_machine_runtime.py`

**Interfaces:**
- Consumes: immutable manifest/task results, signed MAX identity, independent fixture/brief acceptance checks.
- Produces: `machine_completion_gap(evidence) -> str | None`, functional verification manifest bound to source/environment/manifest/data digests; no visual tool or observation.

- [ ] **Step 1: Write no-vision and real functional-negative tests.** Successful functional evidence without any `see` is complete; failing API write, stale evidence after source change, incorrect data readback, user leak or failed migration blocks completion even if screenshot/build succeeds.

```python
def test_vision_is_not_a_machine_completion_requirement(valid_evidence):
    valid_evidence.pop("see", None)
    assert machine_completion_gap(valid_evidence) is None

def test_failed_mutation_blocks_completion(valid_evidence):
    valid_evidence["functional_mutation"] = False
    assert machine_completion_gap(valid_evidence) is not None
```

- [ ] **Step 2: Run RED:** `python -m pytest tests/test_project_machine_completion.py -q`.
- [ ] **Step 3: Implement capability/manifest-based completion and remove inherited machine-path vision instructions.** Run declared build/test tasks in untrusted verification workers; independent verifier captures exit/HTTP/DB/browser evidence and signs outside generated code. Do not impose typecheck on languages without it or static TypeScript source markers on other stacks.

```python
required = ("artifact_identity", "services_ready", "functional_mutation", "persistence", "authorization", "migrations")
return next((name for name in required if evidence.get(name) is not True), None)
```

- [ ] **Step 4: Run GREEN and MAX/native continuation regressions.** Check stale visual-tool replies remain unsupported and cannot start a visual repair loop, while genuine runtime errors remain blocking.

### Task 7: Data-safe candidate publication and public lifecycle

**Files:**
- Create: `apps/api/src/omnia_api/services/project_machine_release.py`
- Create: `apps/api/tests/test_project_machine_release.py`
- Create: `apps/api/migrations/versions/0057_project_machine_releases.py`
- Modify: `apps/api/src/omnia_api/models/project_cell.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_candidates.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_runtime.py`
- Modify: `apps/api/src/omnia_api/routers/runtime.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/nginx_writer.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/project_machine.py`

**Interfaces:**
- Consumes: candidate CAS, session epochs, immutable artifacts, verified data snapshot/restore and supervisor fences.
- Produces: `MachineReleaseCoordinator.prepare`, `publish`, `reconcile`, `rollback`; durable `ProjectMachineRelease` plus `projects.active_release_id` and activation outbox.

- [ ] **Step 1: Write disposable-DB and fake-runtime cutover failure tests.** Concurrent cancel/publish has one winner; last accepted writes appear in new candidate; migration failure leaves old data unchanged; activation crash reconciles committed pair; old jobs cannot write after cutover; rollback cannot discard later writes silently.

```python
async def test_failed_migration_keeps_latest_accepted_write(releases):
    await releases.active_data.insert("late-write")
    releases.migrator.fail_next = True
    with pytest.raises(ReleaseVerificationFailed):
        await releases.publish()
    assert await releases.active_data.contains("late-write")
    assert releases.active_release_id == releases.previous_release_id
```

- [ ] **Step 2: Run RED:** `python -m pytest tests/test_project_machine_release.py -q` with the disposable DB.
- [ ] **Step 3: Implement bounded maintenance/data-copy/migrate/verify/CAS/outbox activation.** Stop requests/jobs and drain writers before final copy. Materialize candidate with final identity, verify it, atomically commit pointer and outbox; reconciliation is idempotent. Enable deploy/stop/logs/start only for machine releases with validated ownership; unimplemented legacy Cell behavior remains explicit 409.

```python
async with store.promotion_lock(project_id, session_id):
    await guard.assert_current_epochs(candidate)
    release = await store.activate_pair_and_enqueue(candidate)
await reconciler.apply_active_release(release.id)
```

- [ ] **Step 4: Run GREEN, candidate fencing, deploy attestation, runtime ownership, checkpoint and migration regressions.** Exercise interruption before/after every durable release transition.

### Task 8: Full deterministic acceptance and delivery handoff

**Files:**
- Create: `apps/orchestrator/tests/test_live_project_machine.py`
- Create: `apps/orchestrator/tests/fixtures/project-machine/node-python/cell.json`
- Create: `apps/orchestrator/tests/fixtures/project-machine/python-web/cell.json`
- Create: `apps/orchestrator/tests/fixtures/project-machine/README.md`
- Modify: this plan with observed acceptance evidence.

**Interfaces:**
- Consumes: qualified disposable Linux Docker environment, disposable application DB, machine/runtime/release APIs, three isolated fixture project identities.
- Produces: reproducible commands and evidence for installed packages, two stack recreations, real DB/API flows, isolation/cancel/release and cleanup inventory; no LLM request.

- [ ] **Step 1: Author fixture code and its assertions before running the new runtime.** Each fixture records an authenticated API item, a worker-generated field and a checksum. Include package import/version and system-tool execution commands. Three cells use distinct values and credentials.

```python
async def test_three_cells_keep_data_after_recreation(live_machines):
    for machine in live_machines:
        await machine.api.create_item(machine.id)
        await machine.capture_and_recreate()
        assert await machine.api.list_items() == [machine.id]
        assert await machine.probe_other_cells() == "denied"
```

- [ ] **Step 2: Run the live suite RED/GREEN on exact test-labelled resources only.** `python -m pytest tests/test_live_project_machine.py -q`; require explicit test endpoint/namespace and deny production-labelled targets.
- [ ] **Step 3: Verify recreation, not only restart.** Remove fixture machine envelopes, restore captured image and volume artifacts, restart manifest services, recheck imports/system tools/source/API/worker data and health. Test one corrupted environment artifact and one failed data migration.
- [ ] **Step 4: Run full touched-service lint/type/tests, migration roundtrip and `git diff --check`.** Parent independently reviews isolation, data cutover and instruction/capability consistency. Resolve findings with regression tests.
- [ ] **Step 5: Give delivery exact branch/files, commit intent, verification and deployment commands.** Delivery agent performs commit/push/documented production rollout. Keep owner-canary flag dark until all runtime gates are observed. Confirm deployed revision/service health; report remaining missing live evidence explicitly rather than claiming completion.

## Acceptance ledger

- [ ] Manifest permits unrelated Linux userland stacks and arbitrary package commands.
- [ ] Installed dependencies/system tools survive full remove/import/recreate.
- [ ] Frontend/backend/worker service state is supervised and resource-accounted.
- [ ] Public installs work; direct/host/platform/metadata/cross-cell egress fails.
- [ ] Custom application schema and authenticated real writes/readback work.
- [ ] Two-user authorization and MAX negative-session cases pass.
- [ ] API/runner restart, pause/cancel and stale lease recovery preserve data and fence old work.
- [ ] All generation excludes `see`; completion and publication use functional evidence only.
- [ ] Publication retains last accepted writes and never mixes runtime/data/jobs revisions.
- [ ] Failed migration/activation and guarded rollback preserve the documented data contract.
- [ ] Three isolated authored cells and two full-stack recreation cases pass without model calls.
- [ ] Review, commit, push, production deployment and health evidence are recorded.
