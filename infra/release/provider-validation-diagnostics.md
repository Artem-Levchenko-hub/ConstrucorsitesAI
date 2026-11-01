# Private, one-shot LLMGW validation evidence

This opt-in diagnostic records a **future** HTTP 422 from the LLMGW chat
completion surface for one explicitly authorized QA project. The earlier failed
wire request was not retained and cannot be reconstructed by this feature.
Capturing evidence neither fixes validation nor proves a successful generation.

Deploy the reviewed revision through the canonical production release path and
confirm the Gateway image/source revision first. This procedure changes no
environment variables, Compose files, startup flags, request payloads, public
errors, provider retry rules, billing, or ambiguity accounting. It makes no
additional provider request. Only the already authorized user action may cause
the next provider POST; do not add a paid diagnostic retry.

## Arm only the current Gateway container

The default is disabled: the application never creates a directory or arm.
The operator must use the **currently deployed Gateway container's private
filesystem**, not the API container, host `/tmp`, a project volume, a public
output directory, or the repository. From a trusted host-root session this can
be accessed through `/proc/<verified-current-gateway-host-pid>/root/tmp/`.
Confirm the container identity, process UID, revision, and PID again before
writing; a redeploy/restart can replace this filesystem and invalidate the arm.

Create `/tmp/yleum-provider-validation` in that container with mode **0700**,
owned by the Gateway process's effective UID. Every path component must be a
directory, with no symlinks. An existing directory must be inspected rather
than replaced or recursively chmod/chowned. Do not follow a symlink to create
or edit diagnostic files.

Create a new regular, single-link **0600**, same-UID `armed.json` there using
exclusive, no-follow creation. Do not overwrite another operator's arm. Its
UTF-8 JSON body must be at most 1024 bytes and contain exactly these fields:

```json
{
  "version": 1,
  "project_id": "00000000-0000-4000-8000-000000000001",
  "nonce": "00000000-0000-4000-8000-000000000002",
  "expires_at": "2026-11-01T12:30:00+00:00"
}
```

The UUIDs and expiry above are examples, not an arm to install. Use the exact
authorized **own QA project** UUID, a new randomly generated UUID nonce, and a
future UTC expiry no more than **one hour** away (prefer 10–20 minutes).
Canonical lowercase hyphenated UUIDs are required. `Z` or `+00:00` UTC offsets
are accepted; naive/local timestamps, unknown fields, and other versions are
rejected. Keep a private operator receipt of project, nonce, expiry, container
identity, deployed revision, ownership and modes, without credentials.

Do not change an existing arm in place. If one already exists, stop and resolve
its ownership with the release owner. Creating or removing a new arm is an
explicit operator action; the application never edits, removes, or rearms it.

## What can be recorded

All conditions must hold:

- The non-streaming public chat route has committed a provider call ID and
  bound valid project, generation-run and provider-call UUIDs to this invocation.
  Context is task-local and follows the existing `asyncio.to_thread` transport.
- Its project UUID matches the unexpired, safe arm.
- The already-dispatched POST received HTTP **422**, on HTTPS
  **`api.llmgw.ru/v1/chat/completions`**, port 443, with no query or URL userinfo.
  The same catch covers normal JSON and the internally collected native stream;
  the latter records the actual `stream: true` request and `stream_options`.
  Other hosts, statuses, endpoints and public streaming routes are out of scope.

An exclusive `claim-<nonce>.json` reserves the arm **before** writing the wire
evidence. One nonce permits at most one attempt across concurrent workers. The
claim remains even if the sink fails: no other call is captured with that arm.
Do not delete claims to force a retry. No old arm, claim, capture or other file
is overwritten. The arm itself is left byte-for-byte unchanged.

On success a complete JSON document is atomically published as
`capture-<nonce>.json`, mode 0600, in the same directory, then never rewritten.
It binds the project/run/call UUIDs and capture time to request method, exact
endpoint, request/response headers, HTTP status and body evidence. Duplicate
header values remain separate entries. Original body SHA-256 hashes and byte
counts are calculated **before** sanitization, not from reconstructed JSON.

The combined original request and response bodies must fit **2 MiB** for text
retention. Above that budget both bodies are explicitly withheld; their exact
hashes and sizes remain. Invalid UTF-8 is likewise withheld without lossy
decoding. There is no silent truncation, no base64 dump of withheld bytes and
no claim that a redacted body is an exact wire byte sequence.

Authorization, cookie, API, token, key and secret header names are fully
redacted. The already available provider key and sensitive header values are
removed from bodies and remaining headers in literal, JSON-escaped,
percent/URL-encoded, base64/URL-safe-base64 and hexadecimal forms. Common
credential-named JSON string values are also redacted. `content_state` is
`exact_utf8`, `redacted`, or `withheld`, with a reason when withheld. This is
targeted credential sanitization, not a guarantee that arbitrary user prompts
contain no private business data or that every custom obfuscation is decoded.
The capture therefore remains **private**, even after redaction.

## Collect, verify, disarm

After the single authorized QA action, inspect only file names, ownership,
modes and the bound metadata first. A claim without a capture means an attempt
was consumed but evidence publication did not complete; preserve it and any
`pending-<nonce>.json` for private diagnosis. The diagnostic deliberately emits
no raw body, capture path, or sink error to application logs or public errors.
Failure to record leaves the original provider exception, cause, retry
decision, accounting and user-visible failure unchanged.

Copy the matching evidence to a closed operator evidence directory with 0700
directory/0600 file permissions, without displaying bodies or keys in the
terminal/chat. Verify file SHA-256, UUID bindings, deployed revision, timestamp,
`content_state`, original byte sizes and body hashes. Review sanitization
privately before giving a support copy to a third party; do not publish the
raw capture in a GitHub issue, repository, public HTML report, or screenshot.

Record the actual observed request count and outcome. A successful request, a
non-422 failure, no matching run/call scope, or no new capture is **not** proof
of a captured validation error. Do not invent the missing old request or label
this as live acceptance without a real accepted UI flow.

The release owner may explicitly remove **only this arm and this nonce's own
files** after private collection/retention decisions and after confirming no
write is in progress. Do not recursively delete the directory or touch another
operator's files. Never remove a claim to permit a paid replay. An expired or
consumed arm is inert even when left in place.

## Verification

MockTransport regression tests exercise the real LLMGW sync and native-stream
422 translation with the exact sent body and unchanged exception chain, and
test the committed dispatch scope. Filesystem/security tests exercise the real
Linux dirfd/O_NOFOLLOW path, modes, ownership, arm expiry/project binding,
concurrent exclusive claims, collision/symlink protection, retained claims on
sink failure, and explicit bounded/non-UTF-8 withholding. The application fails
closed on non-POSIX platforms; Windows transport tests do not stand in for the
Linux filesystem tests. Use exact-revision Linux CI before deployment.
