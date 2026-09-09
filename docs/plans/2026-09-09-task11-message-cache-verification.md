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
passes. Commit/push, CI/image gates and production delivery are still pending.

The browser uses a minimal visible message/status/queue fixture, not the full
ChatPanel. It verifies this hook's behavior, not real transport/auth/model execution.

Raw local commands, fixture and output: `.artifacts/task11-message-cache/`.
Node20/pnpm9.15.0 is the required environment. Backend source is unchanged from
full-green API CI `34356863327` (3264 passed/12 skipped/8 xfailed); this web-only
package must pass its own web and image gates.
