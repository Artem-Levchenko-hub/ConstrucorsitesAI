# Generation stability delivery

This work is delivered in verified slices. The capacity/lifecycle slice does **not**
claim resumable model execution or a complete product-database acceptance gate.

## Capacity and cell lifecycle

- Persist the original resource profile per workspace. Changing the deployment
  default must not change the identity of an existing v1 or v2 cell.
- Bound resource admission from the run's durable `created_at`, including the
  initial ensure and subsequent capacity reclamation. The separate
  `PROJECT_CELL_CAPACITY_WAIT_SECONDS` default is 3600 seconds (30–7200 allowed).
- A cancelled request is not proof that the controller did nothing. Preserve
  dispatched unknown effects as `indeterminate`; use a higher-fence observation.
- An observation of a ready cell can recover its original generation lease.
  An interrupted observation carries that identity into the next reconciliation.
  A completed release must never recreate the lease.
- Repair partially created infrastructure with a new canonical ensure, without
  launching an agent or bootstrap. Retain the original profile and project data.
  Existing confirmed capacity is rebound, not counted twice.
- Reclaim terminal generations only with matching workspace/run ownership and
  no active agent work. Preserve the old binding until physical release is proven.
- Adopt committed but undispatched internal capacity release/pause operations
  using their original operation IDs and checkpoint names.
- A failed release receives a durable, victim-wide retry cooldown. Other
  requesters can reclaim a different idle cell.
- Keep data and dependencies during hibernation. Release CPU/memory reservations
  only when the controller has evidence that compute is stopped or absent.

## No-model resource acceptance

Run the explicit opt-in harness inside the deployed API image:

```sh
/app/.venv/bin/python /app/scripts/verify_generation_capacity.py --execute
```

It creates labeled disposable project/run records under an already eligible
owner, without changing rollout allowlists or invoking a model. It exercises
real resource admission, executor bootstrap, a pinned library installation and
parameterized PostgreSQL write/read in a portable cell, generation release,
targeted hibernation, and controller observation. Legacy cells receive a baseline
tool check, not a false claim of portable package/database capability.

The harness retains its labeled records and evidence. It does not delete existing
projects. A failure is terminalized and cleanup is best-effort; inspect its emitted
stage and durable cell operations before retrying. A passing harness is resource
acceptance, **not** a complete generated application or restore rehearsal.

Use only the production `full` compose project and host orchestrator described in
[project-cell-main-stack.md](project-cell-main-stack.md). Check that no generation
has `finished_at IS NULL`, back up, deploy the exact pushed revision, and verify
API/worker/web/orchestrator revision and health before running acceptance.

## Remaining delivery slices

1. Preserve the full prompt in the UI and durable dispatch.
2. Persist exact native-agent transcript and tool intents/results, fence worker
   ownership, and reconcile stable command IDs before resuming after API restart.
   Blindly restarting the prompt can duplicate SQL or shell effects.
3. Bind finalization to real machine/environment and database archive references;
   synthetic hashes of source/schema files are not database backups.
4. Verify actual product API write/read/update/delete and access isolation, then
   publish code/build/archive as one recoverable, idempotent result.

Current running-generation startup recovery still fails interrupted model work.
Do not treat capacity queue recovery as implementation of full agent resume.

## Deferred review finding

P2: some existing reservation callers acquire a workspace row lock before its
advisory lock, whereas recovery acquires the advisory lock first. Concurrent
transactions can deadlock; PostgreSQL aborts one transaction rather than allowing
conflicting lifecycle effects. Standardizing this lock order across callers is a
follow-up. This release does not claim that every concurrent retry is error-free.
