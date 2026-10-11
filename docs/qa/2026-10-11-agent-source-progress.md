# Legacy agent source progress and completed tool observations

This change is isolated from the generated CRM receipt fixture and live QA. The
source baseline is `2f59e3e34cf72abd6b3236922835b570dd27cc30`. The Windows QA/release
owner retains merge, production delivery and live generation ownership.

## Reproduced failures

A real `run_agent_build` harness reproduced an edit sequence in which two reads
were executed but the third action, a distinct bash patch command, was skipped
before execution. Another harness repeatedly issued different failed edits: the
tool name reset the no-write streak even though no file changed. These reproduce
code behavior; they do not establish the exact arguments or sole cause of any
historical live run whose complete transcript was not retained.

The previous `agent.step` event preceded execution and progress persistence
filled missing `ok` with true. It therefore described an attempt as a successful
tool operation. A skipped `done` could also render as completed work.

## Change and boundaries

All six legacy builder/repair callers receive the existing Cell `snapshot_files`
callback through `AgentOperations`. Each executed action compares independent
copies of the full source tree immediately before and after the action. A
successful real source change resets the no-progress streak. A failed or noop
action does not; cumulative `obs.files` differences from the generation baseline
cannot earn progress. Generic executors without snapshots require the existing
verified full-content change witness instead of an action name or success flag.

The action that reaches the exploration threshold executes and its actual
observation is returned to the model. Further discovery is paused on the next
turn if no source progress occurred; edits and bounded verification remain
available. Total model-step, repeat, rewrite, infrastructure and no-progress
bounds remain. A failed partial source change invalidates prior verification and
same-file read signatures without earning successful progress.

Completed steps carry the actual strict boolean result and a bounded redacted
diagnostic. Guard-skipped steps carry `ok: null` and `skipped: true`; persistence
and both web stream paths preserve that distinction. The UI labels these steps
as skipped rather than completed. No full source snapshots are emitted.

Full build, runtime verification, accepted-snapshot and immutable permit rules
are unchanged. This is not a full functional acceptance contract and does not
fix client receipt loss on a complete MAX browser reload. No credentials,
bindings, CRM records, customer data, quotas or provider configuration change.

## Verification and delivery handoff

Regression red/green checks cover read/read/bash application, a threshold noop
bash followed by a discovery block, failed/noop edit streaks, actual failure
observations, per-action rather than cumulative progress, failed partial mutation
invalidation, and the unchanged total model budget. Existing repeated-read,
rewrite-pressure, green handoff and progress security tests remain in scope.

Local focused API tests: **136 passed**; additional generation caller/progress
contracts on disposable local PostgreSQL: **190 passed**. Full web tests: **116 files / 1070 tests
passed**. API Ruff and mypy (**282 files**) passed; web lint, type checking and
production build passed. Independent bounded source review: **No findings**.
The first full API pass exposed two outdated `AgentOperations` test mocks
without `snapshot_files`; supplying the callback and asserting its exact
forwarding changed those cases from red to green, with no production code delta.
The complete local API suite and exact branch CI are pending at the time of this
checkpoint; their final receipts must be read before merge/delivery. No live
canary or production deployment was performed by this source owner.

Deliver through the existing canonical full production process after exact CI
and a fresh idle/admission check, under the Windows root's sole ownership. The
API/generation worker must contain the new loop and web must contain skipped-step
rendering. Managed SDK, compiled MAX core, kit, database schema and controller
implementation have no source delta; reuse their verified identities only under
the documented release checks. A root live accepted generation after delivery is
still required before claiming live loop behavior or complete product acceptance.
