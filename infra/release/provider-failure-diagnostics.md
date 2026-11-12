# Automatic private provider failure evidence

Gateway HTTP failures now retain a sanitized body in a **private persistent
store**, without an arm or another provider request. The ordinary container
log receives only a `provider.http_failure` summary and an opaque artifact ID,
never the error body. This includes the
native agent `/v1/messages` adapter, whose actual upstream request goes to
`/v1/chat/completions`, as well as JSON, internally collected SSE and public SSE
chat transports. Primary and backup failures produce separate records before
the original accounting and fallback decisions run.

The historical C verification failure at `2026-11-09T22:31:08Z` had a provider
HTTP status of 422, followed by backup HTTP 402. Its original response body and
wire request were not retained. This change cannot recover that evidence or
establish why that particular request was rejected. In particular, a later
insufficient-balance response does not establish the cause of that older 422.

## Request contract

The native adapter preserves OpenAI-shaped tool calls/results, the canonical
Sonnet model ID and its existing output-budget/stream/cache choices. The basic
shape is consistent with the published [chat contract](https://docs.llmgw.ru/usage/chat),
[function-calling guide](https://docs.llmgw.ru/usage/function-calling) and
[Sonnet example](https://llmgw.ru/models/claude-sonnet-5.5); it is not proof of
the missing historical request's exact contents.

Converted native requests are now checked **before provider admission**.
Malformed content, empty/orphan/duplicate tool IDs, unresolved tool calls and
invalid tool argument objects produce a fixed local 400 without exposing input
values or making a paid POST. Valid multimodal/cache content and mixed tool
results followed by user feedback remain supported. The validator does not
rewrite transcripts, invent tool results or change model parameters to guess
away an unknown provider error.

Search the Gateway container log for `native_messages.request_rejected` and
the generation's `run_id` to locate a local HTTP 400. Each record includes
`error_origin: gateway`, the route, stage, retry count, and a fixed validation
phase, reason and field path. For example, `unresolved_tool_calls` at
`messages[12].role` means a new message arrived before pending tool results.
The `transcript_validation` phase indexes the converted provider request.
Provider configuration failures have a null field path. These records contain
structural metadata and UUIDs. The preceding provider receipt, when available,
is in the daily request log for the same run.

## Fields and boundaries

Each private record contains:

- Provider scope and observed upstream HTTP status.
- Committed project/run/call UUIDs when the request has a complete valid scope.
- Request method and a fixed endpoint class, never a URL, query or userinfo.
- SHA-256 and byte count of the actual serialized request, without its text.
- Bounded request shape: known field presence/types, fixed model class, message
  role/content-type counts and tool count. Unknown field names, prompts, tool
  arguments and application source are not included.
- A fixed whitelist of syntactically bounded request/response trace-ID headers.
  Authorization, cookie and arbitrary header values are not exported.
- SHA-256 and byte count of the original, already-read provider error body.
  For a valid UTF-8 body of at most 16 KiB, the complete sanitized text is kept.
  Known credential spellings, credential-labelled values, bearer credentials
  and URLs are removed. Duplicate JSON error fields remain distinguishable.

The body state is `exact_utf8`, `redacted`, `withheld` or `unavailable`. Redacted
text is not an exact wire copy: the hash and byte count describe the **original**
body. Oversized, invalid-UTF-8 or failed-sanitization bodies are explicitly
withheld with a reason. An unread stream is not consumed for diagnosis; its
unknown body hash/size are null. No prefix is silently labelled a full body.

These private records may still contain provider or user text after credential
redaction. They must not be returned in API error details, ordinary application
logs, public reports, screenshots or GitHub issues. Public refusal text, retry permission, monetary
ambiguity, ledger writes and billing are unchanged. A failing log sink cannot
replace the original error or cause another provider request.

## Collecting evidence

The canonical production Gateway mounts the dedicated named volume
`gateway-provider-failures` at `/var/lib/yleum/provider-failures`. The image
provisions its empty directory as process-owned mode 0700. Private files use
mode 0600, no-follow directory-relative access, exclusive/atomic publication,
and no overwriting of another record. The store is separate from project
volumes and persists across container replacement. It has a 128 KiB per-record,
64 MiB total and 1024-file capacity bound; it does not silently delete old
incidents to make room. Unsafe permissions, unsupported filesystems, capacity
exhaustion or a failed sink produce an explicit unavailable/withheld state in
the metadata summary rather than an error-body dump to stdout or a model retry.

Use a trusted host/operator session and the currently verified Gateway
container identity. Filter `provider.http_failure` summaries by the exact
project/run/call UUID and time, then collect the matching private artifact.
Preserve **all** primary and backup records rather than just the last error.
Verify the artifact binding, file SHA-256, original request/response hashes,
body state and permissions before privately reviewing or sharing a support copy.
Never print a whole private artifact into chat or publish it in a report.

Container-log rotation can remove the summary, but not the stored artifact.
Archive and remove records only through an explicit operator retention decision,
after verifying their IDs and scope; do not prune customer volumes, overwrite
records or recursively delete this store. Removing the dedicated Docker volume
also removes its retained evidence. A failed disk or missing/full store cannot
provide a body-retention guarantee; the summary makes that limitation visible.

For an explicitly authorized, one-shot **full wire request** capture, including
messages, use [the separate private capture procedure](provider-validation-diagnostics.md).
That mechanism remains opt-in and is not needed for the automatic error body
event. Do not run a paid request merely to populate diagnostics.

## AI-free verification

Gateway tests use synthetic `httpx.MockTransport` responses, with unmocked HTTP
forbidden. They check complete 422 text, actual serialized request hashes,
native-agent conversion, distinct 422/402 fallback records, scope isolation,
secret redaction, explicit withholding and unchanged public/accounting behavior.
This is not successful live generation acceptance or a reconstruction of the
historical 422.
