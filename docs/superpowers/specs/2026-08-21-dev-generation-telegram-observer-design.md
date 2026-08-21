# Dev generation Telegram observer — design

**Date:** 2026-08-21

**Scope:** every real `build` and `edit` generation in the current development environment

**Status:** approved in chat (two-stage Telegram thread, exact user-authored message, preview screenshot, durable delivery, release kill switch)

## Outcome

The team Telegram group receives a two-stage report for every accepted development `build` or `edit` generation:

1. a start message containing the project name, generation mode, short run id, and the exact message typed by the user after credential redaction;
2. a reply containing either the completed preview screenshot or the stage and sanitized reason for failure/cancellation.

The observer is diagnostic only. Telegram availability must never delay, fail, cancel, or change a user generation. Reports survive API/worker restarts and transient Telegram outages. A single configuration switch disables creation and delivery of all per-generation reports before the product is released.

The existing daily production-canary summary remains separate. The observer may report the canary account's individual `build` and `edit` runs like any other generation; the daily canary workflow still sends its one aggregate result.

## Selected product behavior

### Start message

The observer sends one message after the generation request and its chat rows commit:

```text
🟡 BUILD · Проект «Название» · #a1b2c3d4
Промпт:
<сообщение, которое пользователь сам отправил>
```

`EDIT` replaces `BUILD` for edit runs. The prompt source is exclusively `GenerationRun.user_message_id -> Message.content`. The observer never sends the effective compiled brief, discovery output, system prompt, agent instructions, chat history, account email, user id, or billing identity.

Project names and other labels are normalized for control characters and bounded in length before formatting. They cannot inject extra fields or Telegram markup.

`redact_provider_secrets` is applied again at the delivery boundary even though credential intake already protects persisted chat. If the resulting message does not fit in Telegram's message limit, the start message contains a short preview and the complete redacted user message is sent as a UTF-8 `.txt` document replying to the start. No user-authored text is silently discarded.

### Successful completion

The final report is a reply to the start message. For a run that commits a new snapshot, it is a Telegram photo using the PNG produced by the existing preview worker:

```text
✅ BUILD завершён · 08:42 · #a1b2c3d4
```

The PNG is loaded through the internal MinIO client and uploaded to Telegram as bytes. The report never exposes MinIO credentials, internal object keys, public preview URLs, signed preview-session URLs, or cookies.

An edit that legitimately completes without a new snapshot receives a text reply labelled `✅ EDIT завершён без нового снимка`; it does not reuse an old screenshot and present it as the result of the edit.

### Failure and cancellation

A failed run receives a text reply:

```text
❌ BUILD упал · этап: writer · 03:17 · #a1b2c3d4
Ошибка: <очищенная причина>
```

A user cancellation receives `⚪ BUILD отменён пользователем` or `⚪ EDIT отменён пользователем`.

The observer records a small normalized stage vocabulary rather than copying every progress event:

- `accepted`;
- `routing`;
- `director`;
- `writer`;
- `images`;
- `acceptance`;
- `snapshot`;
- `preview`.

The last recorded stage is included on failure. The error text is derived from `GenerationRun.error` or a normalized product failure reason, then passed through credential, URL/query, email, control-character, and length redaction. Telegram never receives a traceback, response body, cookie, signed URL, provider token, database DSN, or Telegram API error body. The exception class and a bounded sanitized message may remain because this is a temporary internal development observer intended to make failures actionable.

### Preview failure

Generation success and preview success remain distinct. A successful run waits up to five minutes for its new snapshot's `preview_key`:

- preview ready within the window: send the success photo;
- no preview by the deadline: send `⚠️ BUILD готов, но preview не получен` with the normalized preview failure/timeout reason;
- preview becomes ready after the warning: send one late photo reply labelled `🖼 Preview появился позже`.

This makes preview failures visible without changing the generation's product status.

## Considered approaches

### 1. Direct Telegram calls from the generation coroutine

This is the smallest implementation but introduces an external network dependency into user generation. It can extend response/finalization latency, loses notifications on process restarts, and creates duplicate sends when a coroutine is retried. It is rejected.

### 2. Redis/RQ jobs without durable report state

This keeps Telegram off the request path and reuses the worker, but Redis loss or a missed enqueue can permanently lose a report. It also lacks a reliable source for reconciliation and duplicate suppression. That does not satisfy the requirement to cover every generation.

### 3. Durable database report state plus a dedicated delivery loop — selected

The generation transaction creates durable report state, and a small dedicated worker polls due rows and performs Telegram I/O. PostgreSQL is both the durable state and work queue; Redis is not involved in report delivery. Row leases support safe restart and future horizontal scaling. This provides non-blocking delivery, bounded retries, and an auditable answer for every run without depending on an ephemeral wake-up event.

Telegram does not provide an idempotency key for `sendMessage`/`sendPhoto`. The design suppresses ordinary duplicates using database state and row leases, but one rare duplicate remains possible if Telegram accepts a message and the worker crashes before storing the returned message id. The implementation must document this external side-effect window rather than claiming impossible exactly-once delivery.

## Data model

Add a `generation_telegram_reports` table with one row per observed run:

- `run_id` — UUID primary key and foreign key to `generation_runs.id` with cascade delete;
- `start_state` — `pending`, `sending`, `sent`, `failed`, or `suppressed`;
- `start_message_id` — Telegram message id, nullable;
- `finish_state` — `waiting_terminal`, `waiting_preview`, `pending`, `sending`, `sent`, `warning_sent`, `failed`, or `suppressed`;
- `terminal_status` — nullable `completed`, `failed`, or `cancelled`;
- `last_stage` — normalized stage, initially `accepted`;
- separate start/finish attempt counters and `next_attempt_at` timestamps — bounded retry state for each event;
- `lease_until` — recovery for a worker that dies while sending;
- `last_delivery_error_code` — fixed local category only, never a Telegram response body;
- `preview_deadline_at` — five-minute deadline for a successful snapshot preview;
- timestamps.

The table does not duplicate prompts, account data, generated files, screenshots, exception bodies, preview URLs, or credentials. Delivery loads the current source records by id. `GenerationRun`, `Message`, `Project`, `Snapshot`, and MinIO remain the authoritative data sources.

The row is created only after `turn_mode` is known and only for `build` or `edit`. Clarification interviews, onboarding questions, installer/help responses, credential redirects, and other text-only turns create no report.

## Lifecycle and transaction boundaries

### Acceptance

`post_prompt` creates the report row in the same outer transaction that persists the `GenerationRun`, user message, assistant message, `user_message_id`, and `response_mode`. The optional observer insert is protected by a nested savepoint: a local observer defect can roll back that insert and be logged without poisoning prompt acceptance, while every successful insert commits atomically with the run. The delivery worker discovers the committed pending row through polling; there is no enqueue gap.

### Stage recording

A focused observer service exposes `record_stage(run_id, stage)`. Existing progress points call it only on normalized stage transitions, not for token deltas or every internal event. Stage persistence is best-effort and must never abort generation; if it fails, the previous stage remains.

### Terminal state

The same transaction that changes a run to `completed`, `failed`, or `cancelled` also updates its report row. This rule applies to:

- normal `finalize_generation_run`;
- exception-driven `set_generation_run_status`;
- user cancellation;
- interrupted-run recovery on API startup;
- the existing active-run self-heal path.

For completed runs, the observer resolves the assistant message's `snapshot_id`. A real snapshot moves the report to `waiting_preview`; a legitimate no-snapshot edit moves directly to `pending`. Failed and cancelled runs move directly to `pending`.

### Preview callback

The existing preview worker remains the sole screenshot renderer. After it commits `Snapshot.preview_key` and publishes `preview.ready`, it updates the report state for the generation whose assistant message owns that snapshot. The polling report worker discovers that state; the preview worker does not call Telegram directly.

Preview exits that currently return without an image must produce a normalized local outcome for observer diagnostics. This outcome affects only the report. The five-minute reconciliation deadline guarantees a final warning even if no preview callback arrives.

## Delivery worker and reconciliation

A new focused `generation-report-worker` process owns Telegram formatting and delivery. It:

1. polls PostgreSQL for due rows at a short bounded interval;
2. claims rows with `FOR UPDATE SKIP LOCKED` and a database lease;
3. rechecks the kill switch before any external call;
4. sends the start message/document or terminal reply/photo;
5. persists Telegram message ids and state;
6. retries transient network/5xx/rate-limit failures using `next_attempt_at` and bounded backoff;
7. marks permanent configuration/4xx failures with a fixed local error category.

The worker sends only to the configured fixed group id and the fixed Telegram API origin. Neither value can come from a user request, project content, or database row.

Finish delivery is eligible only after `start_message_id` is durable, so every result can reply to its own start. A permanently failed start keeps the finish pending for operator replay rather than emitting an unthreaded result.

The process is added as a separate production-style Compose service using the API image and a focused module entry point. It performs no generation, preview rendering, or Redis queue work. On startup and every poll it considers expired leases, pending starts, terminal reports, preview deadlines, and retry timestamps. Database state therefore survives Redis loss, worker restarts, and missed in-process callbacks without needing a separate scheduler.

Delivery attempts are capped per event. Exhausted rows stay visible as `failed` for operational inspection and can be replayed after configuration is repaired. Telegram failure is logged only as run id, event type, HTTP class, and local error code; response bodies and token-bearing request URLs are never logged.

## Configuration and release switch

Add runtime settings:

- `DEV_GENERATION_TELEGRAM_REPORTS` — default `false`;
- `TELEGRAM_BOT_TOKEN` — secret;
- `TELEGRAM_CHAT_ID` — fixed negative group id.

The feature requires the flag plus valid token and group id. The API receives the flag; the dedicated report worker receives the flag, token, group id, database, and MinIO settings through the documented production-style Compose environment. The preview worker needs only the flag/database behavior required to update report state. The existing GitHub Actions environment secrets used by the daily canary do not automatically configure runtime containers; deployment must explicitly add the values to the runtime secret source without printing them.

The kill switch is checked both when a report row would be created and immediately before delivery. Switching it to `false` stops new rows and marks already pending work `suppressed`, so delayed jobs cannot leak messages after release. Before public release, the runbook requires setting the flag to `false`, recreating API/worker containers, and proving that a test build creates no Telegram report.

## Security and privacy

- Only the exact user-authored `Message.content` for the current run is reported.
- `redact_provider_secrets` runs at credential intake and again at Telegram delivery.
- Account email, user id, business identity, billing data, full chat history, system prompts, compiled briefs, generated source, logs, cookies, signed URLs, and response bodies are excluded.
- Long prompts are attached from in-memory redacted bytes; no temporary prompt file persists beyond the worker job.
- Screenshots come from the existing generated preview and are uploaded as bytes, not by a public or signed URL.
- All Telegram calls use bounded connect/read timeouts and a fixed host.
- The Telegram group is treated as an external retention boundary. This observer is intentionally development-only and disabled by default.

## Failure isolation

Observer operations never participate in the success decision for a user generation. Database report-row creation is small, local, and guarded by a nested savepoint; if it unexpectedly fails, prompt acceptance logs and omits observability without poisoning the outer transaction. All stage updates, terminal observer updates, MinIO reads for Telegram, polling, and Telegram calls are fail-isolated from generation and preview status.

Deleting a project cascades its report rows with the generation runs. A poll cycle that observes no row sends nothing.

## Testing

### Unit tests

- start/final formatting for build, edit, failure, cancellation, preview warning, and late preview;
- exact selection of `user_message_id -> Message.content` rather than effective/system prompts;
- credential and error redaction, including the current Telegram/provider token shapes, labelled secrets, signed URLs, emails, control characters, and length caps;
- long-prompt message plus complete UTF-8 document behavior;
- fixed Telegram host/group request construction and bounded timeouts;
- normalized stage transitions;
- retry classification and backoff limits.

### Database/service tests

- one report row for each accepted build/edit and none for clarify/text turns;
- report creation in the accepted-run transaction;
- terminal transitions for success, failed build without snapshot, exception, cancellation, startup recovery, and active-run self-heal;
- ordinary duplicate suppression and lease recovery;
- pending-row reconciliation after worker downtime and Redis reset;
- kill switch suppressing both new and already pending work;
- project deletion removing report state safely.

### Worker/integration tests

- successful preview callback sends one photo reply using MinIO bytes;
- preview timeout sends a warning and a later callback sends at most one late photo;
- Telegram timeout/429/5xx retries without affecting generation;
- permanent Telegram failure records a local code without logging response content or token URLs;
- worker restart resumes pending rows;
- malformed/missing snapshot data fails safely.

### Live development acceptance

After migration and configuration:

1. run one successful build and verify the start message plus screenshot reply;
2. run one successful edit and verify the same thread behavior;
3. trigger a controlled failure and verify its stage and sanitized reason;
4. cancel one run and verify the cancellation reply;
5. temporarily block Telegram delivery, restore it, and verify reconciliation;
6. set `DEV_GENERATION_TELEGRAM_REPORTS=false`, recreate API/worker, run a build, and verify no report is created or sent;
7. re-enable only while the environment remains internal development.

## Operational visibility

Structured logs and counters distinguish `start_sent`, `finish_sent`, `preview_warning`, `retry`, `failed`, and `suppressed`. They use short run id and event type only. The database table is the authoritative audit surface for missing reports; Telegram chat history is not used as a queue or state store.

## Out of scope

- production/customer generation monitoring after public release;
- sending source files, chat history, system prompts, compiled briefs, account identity, or raw logs to Telegram;
- screenshots generated by a second browser pipeline;
- a Telegram command/control bot that can retry, cancel, or modify generations;
- analytics dashboards, alert routing by team member, or per-project channels;
- exactly-once guarantees across the unavoidable Telegram-ack/database-commit crash window.

Those require a separate privacy and operations review if the development observer proves useful.
