# Task11: remove repeated single-message cache updates

BASE `6c3b9babd669a45c6b60cd6666ba9657cc5c7379`. Implementation, independent
review and delivery use GPT-6 Astra. The owner requires smaller, readable code
with all existing functionality preserved.

## Change and contract

Nine repeated `setQueryData`/message-ID maps in `usePromptStream` use one typed
local `updateMessage` callback. Event-specific transformations and their order
remain at the callers. There is no new production module, dependency, framework
or terminal-handler merger. Optimistic insertion and list filtering stay intact.

Production source shrinks by **32 lines**, or **1,101 UTF-8 bytes after normalizing
line endings** (1,170 raw bytes). Existing mixed endings of untouched lines remain.
The callback accepts `string | null`,
matching existing cancellation comparisons, and depends on the same `qc` and
`projectId` identities. The three callbacks using it include it in their dependency
lists. An absent cache still becomes an empty array; every duplicate matching ID
is transformed; unmatched messages retain their object identities. The final
cancel path retains the assistant-role guard and its message-ID side effect.

Stream synchronization, chunks/replay/gaps, terminal success/error/cancellation,
temporary assistant-ID replacement, local cancellation and the next queued
request retain their existing behavior. Known unmount/stale-probe and ambiguous
POST outcomes are separate behavior changes and are not addressed here.

## Verification

The same seven focused suites pass BEFORE/AFTER: **47 tests**, including six new
contracts exercised against the actual hook and QueryClient. TypeScript and
focused ESLint pass. The browser fixture runs that same hook with controlled
HTTP/WebSocket responses at 1440 and 390 pixels; BEFORE/AFTER JSON is identical,
with no page errors and exactly one queued POST. No live model request is used.

Full web suite on Node20: **502 passed, 78 suites**, exit0, 115.90s. The first run,
concurrent with browser/typecheck/lint, had two failures in existing history
navigation tests in `max-data-entry` (500/502); the same tests passed unchanged
once that load stopped. The cause is not established. Both logs are retained.
No product/test change hides those failures.

Independent Astra source/tests and scoped publication-helper review: No findings.
Private delivery-helper review identified missing HTTP timeouts and rollback gate
readback; both were fixed and re-reviewed with no remaining findings. Bash syntax
passes. Source `bf4284569e75eb40c3fc6fd81e67a83dbb7e57a0` is committed and pushed
to main. CI `34363742655`: web, image-build, py-syntax, gateway-tests,
workflow-lint and orchestrator-release-gate passed. API gate had not finished when these
web gates were recorded; no result is claimed. Backend source is unchanged.

The browser uses a minimal visible message/status/queue fixture, not the full
ChatPanel. It verifies this hook's behavior, not real transport/auth/model execution.

Raw local commands, fixture and output: `.artifacts/task11-message-cache/`.
Node20/pnpm9.15.0 is the required environment. Backend source is unchanged from
full-green API CI `34356863327` (3264 passed/12 skipped/8 xfailed); this web-only
package must pass its own web and image gates.

## Production delivery blocked by repeated manual backend stops

Initial preparation stopped with exit22 on public API HTTP502, before building,
merging the server checkout, changing the write gate or restarting any service.
Evidence: `/opt/omnia-runtime/releases/task11-message-cache-bf428456`.
Its local web BEFORE response still identifies healthy `2ae98520`; the public
API BEFORE file is empty because that request failed.

Read-only diagnosis at approximately 14:30 UTC on September9 confirmed all three
backend containers were manually stopped before this delivery attempt. Docker
journal records `hasBeenManuallyStopped=true`, `restart canceled` and
`daemonShuttingDown=false`. API and worker exited0 at14:22:34; generation-worker
received SIGTERM, exceeded30 seconds and exited137 at14:23:03. `OOMKilled=false`.
API passed its last health probe at14:22:33. The image remains
`sha256:47ac83c1f70ee7351812128fcfc56869e8bdd020fe3143ff45207c4fb2cf4664`,
release `50565efd`. This was not established as an application crash.

The same three backend containers were externally restarted at14:30:42–43.
Their IDs/image/release and all six public health checks were verified again;
this task performed no backend recovery. Preparation then succeeded in
`/opt/omnia-runtime/releases/task11-message-cache-bf428456-retry1`: exact archive
build and network-none/read-only image startup smoke passed. Built image:
`sha256:e027539f89266fce09c339a8c1d3742dc62c2715ba1709bbaa5a0d378d04e405`.

Docker journal confirms a second manual stop: API/worker at14:36:29, generation
worker at14:36:58 after its30-second stop grace period. The new web switch started
at14:37:10 under the verified write gate and zero-activity guard. The helper's
unchanged-container check compared IDs/images/StartedAt but missed stopped state.
New web passed exact image/release health; the subsequent API check failed.

Automatic recovery restored web `2ae98520` and its exact old image, confirmed
healthy locally and publicly. Nginx and Compose env bytes match their backups.
The final POST405 check could not pass because API returned502. Do not claim a
successful rollout or successful HTTP gate readback. `switch.complete` is absent;
the retry1 `result.json` records `rolled_back`. H149 is still unpublished.

Another Codex task is active on the same repository; the operator responsible
for these stops is not established. Maintenance ownership must be resolved
explicitly before another attempt; a transient return to healthy is insufficient.
Clarification is pending. This task must not restart backend services while that
ownership remains unclear.

For resumption, re-establish current local/remote/server revisions and ownership,
then backend health. The private helper now also compares non-web `State.Status`
and checks all six API health results/release/dependencies immediately before the
write gate; syntax passed, but the modified helper has not been executed. It does
not replace coordination with another operator. Prepare a new exact revision and
release record; preserve both failed records. Finish web image/release/health and
scoped H149 publication before starting another refactoring package.
