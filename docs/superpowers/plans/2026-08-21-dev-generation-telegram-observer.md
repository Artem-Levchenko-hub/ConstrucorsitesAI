# Dev Generation Telegram Observer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report every accepted development `build` and `edit` generation to the team Telegram group with the exact redacted user-authored message at start and a threaded preview screenshot or sanitized terminal error at finish, without making Telegram part of the generation success path.

**Architecture:** Persist one observer row per eligible `GenerationRun` in PostgreSQL, update it from existing generation and preview lifecycle transactions, and deliver due events from a dedicated database-polling process that reuses the API image. The delivery process reads authoritative `Message`, `Project`, and `Snapshot` rows at send time, uploads existing preview PNG bytes from MinIO, and uses leases plus bounded retries for restart safety. Redis/RQ remains responsible only for preview rendering and is not part of Telegram delivery.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL, httpx, MinIO, Docker Compose, pytest, Ruff, mypy, GitHub Actions, Telegram Bot API 10.2.

**Spec:** `docs/superpowers/specs/2026-08-21-dev-generation-telegram-observer-design.md`

## Global Constraints

- `DEV_GENERATION_TELEGRAM_REPORTS` defaults to `false`. New reports and external sends require it to be `true`.
- Only `GenerationRun.user_message_id -> Message.content` is eligible as prompt text. Do not read the compiled prompt, history, discovery brief, system prompt, source files, account identity, or billing records.
- Telegram failures must not change a generation, assistant message, snapshot, preview, wallet, or HTTP response. All observer hooks are fail-soft and transactionally isolated with savepoints where they share a generation transaction.
- Telegram delivery uses the fixed origin `https://api.telegram.org`, a configured negative numeric `TELEGRAM_CHAT_ID`, and bounded connect/read/write/pool timeouts. Project or user content must never influence the host, bot method, or target chat.
- Logs and database diagnostics may contain only short run id, observer event, state, attempt count, HTTP status class, and fixed local error code. Never log the bot token, Telegram request URL, response body, prompt, email, MinIO key, signed URL, cookie, DSN, or traceback text from a provider response.
- Do not add a second screenshot renderer. The report worker reads the PNG created by `omnia_api.workers.preview` from the configured previews bucket.
- Telegram has no idempotency key for send methods. Database leases suppress ordinary duplicates, but the accepted-send-before-DB-commit crash window must remain documented and tested as an acknowledged limitation.
- Follow red-green-refactor. Each implementation task starts with the listed failing test, observes the intended failure, makes the smallest production change, reruns the focused tests, then commits.
- Do not deploy from this feature branch. Merge a reviewed PR to protected `main`, run the full local release gate at the exact merge SHA, then follow `infra/release/README.md`. The runbook still requires the owner to confirm exact 40-character release and rollback SHAs before production access.
- The bot token previously pasted into chat must not be copied into source, docs, shell history, command-line arguments, CI logs, or plan evidence. Rotate it in BotFather before live enablement and inject the replacement through the protected runtime secret path.

---

## Task 1: Add durable observer state and disabled-by-default settings

**Files:**

- Create: `apps/api/migrations/versions/0047_generation_telegram_reports.py`
- Create: `apps/api/src/omnia_api/models/generation_telegram_report.py`
- Modify: `apps/api/src/omnia_api/models/__init__.py`
- Modify: `apps/api/src/omnia_api/core/config.py`
- Create: `apps/api/tests/test_generation_telegram_reports.py`

### Data contract

Create `GenerationTelegramReport` with this schema:

| Column | Type | Required behavior |
|---|---|---|
| `run_id` | PostgreSQL UUID, PK/FK | `generation_runs.id`, `ON DELETE CASCADE` |
| `start_state` | text | `pending`, `sending`, `sent`, `failed`, `suppressed`; default `pending` |
| `start_message_id` | bigint nullable | Telegram message id; retained while a long-prompt document is retried |
| `finish_state` | text | `waiting_terminal`, `waiting_preview`, `pending`, `sending`, `sent`, `warning_sent`, `failed`, `suppressed`; default `waiting_terminal` |
| `terminal_status` | text nullable | `completed`, `failed`, `cancelled` |
| `last_stage` | text | `accepted`, `routing`, `director`, `writer`, `images`, `acceptance`, `snapshot`, `preview`; default `accepted` |
| `start_attempts` / `finish_attempts` | integer | non-negative, default `0` |
| `start_next_attempt_at` / `finish_next_attempt_at` | timestamptz nullable | next eligible attempt |
| `lease_until` | timestamptz nullable | short claim lease shared by the single active event |
| `last_delivery_error_code` | text nullable | fixed local category only |
| `preview_error_code` | text nullable | fixed local preview category only |
| `preview_deadline_at` | timestamptz nullable | five minutes after successful run with a snapshot |
| `created_at` / `updated_at` | timestamptz | server timestamps; `updated_at` maintained by the existing database trigger function |

Add check constraints for every finite state/stage/status set and non-negative attempts. Add a due-work index over `start_state`, `finish_state`, the two next-attempt timestamps, and `lease_until`. Install a table-specific `BEFORE UPDATE` trigger using the existing `set_updated_at()` database function, and drop that trigger before the table in downgrade. Do not store prompt text, screenshot data, project name, user id, error text, preview key, chat id, or token in this table.

Add these settings to `Settings`:

```python
dev_generation_telegram_reports: bool = Field(default=False)
telegram_bot_token: SecretStr | None = Field(default=None)
telegram_chat_id: int = Field(default=0)
```

`0` is a disabled sentinel that lets the dedicated process boot with the feature
off. `TelegramBotClient` rejects it whenever delivery is enabled and requires a
negative group or supergroup id.

- [ ] Write failing metadata/default tests in `test_generation_telegram_reports.py`: the feature flag is false; the report table has one cascade FK to `generation_runs`; all state defaults and constraints are present; deleting a `GenerationRun` cascades its report row.
- [ ] Run the focused tests and confirm they fail because the model/settings do not exist:

```bash
cd apps/api
uv run pytest -q tests/test_generation_telegram_reports.py
```

- [ ] Implement the ORM model, export it from `models/__init__.py`, and add the settings exactly as above.
- [ ] Add Alembic revision `0047_generation_telegram_reports` with `down_revision = "0046_project_memory"`. Keep the downgrade limited to dropping the new table and its indexes.
- [ ] Prove the migration upgrades a local disposable database and that ORM metadata is in sync:

```bash
cd apps/api
uv run alembic upgrade head
uv run pytest -q tests/test_generation_telegram_reports.py
uv run ruff check migrations/versions/0047_generation_telegram_reports.py src/omnia_api/models/generation_telegram_report.py src/omnia_api/models/__init__.py src/omnia_api/core/config.py tests/test_generation_telegram_reports.py
```

- [ ] Commit the persistence slice:

```bash
git add apps/api/migrations/versions/0047_generation_telegram_reports.py apps/api/src/omnia_api/models/generation_telegram_report.py apps/api/src/omnia_api/models/__init__.py apps/api/src/omnia_api/core/config.py apps/api/tests/test_generation_telegram_reports.py
git commit -m "feat(api): persist generation Telegram report state"
```

---

## Task 2: Build the redaction, formatting, and bounded Telegram transport boundary

**Files:**

- Modify: `apps/api/src/omnia_api/services/secret_safety.py`
- Modify: `apps/api/tests/test_secret_safety.py`
- Create: `apps/api/src/omnia_api/services/generation_telegram_delivery.py`
- Create: `apps/api/tests/test_generation_telegram_delivery.py`

### Public service surface

Implement these value objects and functions in
`generation_telegram_delivery.py`:

```python
@dataclass(frozen=True)
class StartDelivery:
    text: str
    prompt_document: bytes | None
    prompt_filename: str | None


class TelegramFailure(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
```

Add complete implementations for `build_start_delivery(run_id, mode,
project_name, user_text)`, `build_finish_text(run_id, mode, outcome,
elapsed_seconds, stage, error, preview_error_code)`, `sanitize_error(value)`, and
`TelegramBotClient.send_message`, `send_document`, and `send_photo`. The tests
must call the same keyword arguments. Committed code must not contain unfinished
branches.

### Required formatting and transport behavior

Protocol reference: [official Telegram Bot API](https://core.telegram.org/bots/api).

- Extend `_SECRET_TOKEN_PATTERNS` with a high-confidence Telegram bot token shape: 8–12 decimal digits, a colon, then at least 30 URL-safe token characters.
- `build_start_delivery` calls `redact_provider_secrets` at the delivery boundary, normalizes control characters in labels, and does not redact ordinary prompt emails or URLs unless they match a credential. This preserves what the user actually typed.
- Use plain text with no `parse_mode`. Disable link previews. Keep `sendMessage` under the documented 4096-character limit with a conservative internal ceiling of 3600 characters.
- If the complete redacted prompt does not fit, put a clearly labelled bounded preview in `text` and the complete redacted UTF-8 bytes in `prompt_document`; a run whose short id is `a1b2c3d4` uses the safe filename `generation-a1b2c3d4-prompt.txt`.
- `sanitize_error` reapplies credential redaction, replaces emails, URL query/fragment data, DSNs, control characters, and line breaks, then caps output at 500 characters. It must never return a traceback or response body.
- The client constructor rejects a non-negative chat id and an empty token. It owns an `httpx.AsyncClient` with fixed Bot API origin and `httpx.Timeout(connect=5, read=15, write=15, pool=5)`.
- Send JSON for `sendMessage`; send multipart bytes for `sendDocument` and `sendPhoto`. A reply to Telegram message `12345` uses `reply_parameters={"message_id": 12345}`; never use a user-supplied thread/chat target.
- Parse only `ok`, `result.message_id`, `error_code`, and `parameters.retry_after`. Never include Telegram's `description` or raw response body in `TelegramFailure`, logs, database state, or assertions.
- Classify network/timeouts, 429, and 5xx as retryable. Respect bounded `retry_after`. Treat invalid configuration, 400/401/403/404, and malformed successful responses as permanent fixed codes.

- [ ] Add failing tests for Telegram token redaction, labelled secrets, project-name control characters, exact prompt preservation, long UTF-8 prompt attachment, 3600/4096 boundaries, error sanitization, negative chat-id validation, fixed host/chat, `reply_parameters`, multipart PNG/TXT upload, bounded timeout, 429 retry-after, 5xx retry, and permanent 4xx classification.
- [ ] Run and observe the expected missing-module/redaction failures:

```bash
cd apps/api
uv run pytest -q tests/test_secret_safety.py tests/test_generation_telegram_delivery.py
```

- [ ] Implement the pure formatting helpers and transport. Use `httpx.MockTransport` in tests so no test contacts Telegram.
- [ ] Rerun the focused tests and static checks:

```bash
cd apps/api
uv run pytest -q tests/test_secret_safety.py tests/test_generation_telegram_delivery.py
uv run ruff check src/omnia_api/services/secret_safety.py src/omnia_api/services/generation_telegram_delivery.py tests/test_secret_safety.py tests/test_generation_telegram_delivery.py
uv run mypy src/omnia_api/services/generation_telegram_delivery.py
```

- [ ] Commit the delivery boundary:

```bash
git add apps/api/src/omnia_api/services/secret_safety.py apps/api/src/omnia_api/services/generation_telegram_delivery.py apps/api/tests/test_secret_safety.py apps/api/tests/test_generation_telegram_delivery.py
git commit -m "feat(api): add safe Telegram generation delivery"
```

---

## Task 3: Create fail-soft report lifecycle services

**Files:**

- Create: `apps/api/src/omnia_api/services/generation_telegram_reports.py`
- Extend: `apps/api/tests/test_generation_telegram_reports.py`

### Service contract

Implement this focused async surface:

```python
REPORTABLE_MODES = frozenset({"build", "edit"})
REPORT_STAGES = (
    "accepted", "routing", "director", "writer", "images",
    "acceptance", "snapshot", "preview",
)
```

- `create_report_for_run(session, run, *, enabled) -> bool`
- `record_report_stage(run_id, stage) -> None`
- `sync_terminal_report(session, run, *, enabled) -> None`
- `mark_snapshot_preview_ready(session, snapshot_id) -> None`
- `mark_snapshot_preview_failed(session, snapshot_id, code) -> None`
- `suppress_pending_reports(session) -> int`

The committed implementation must contain complete bodies and no unfinished
branches.

### Lifecycle rules

- `create_report_for_run` returns false for a disabled flag or a mode outside `build/edit`. For eligible runs it inserts exactly one row inside `session.begin_nested()` and flushes it. An observer insert error rolls back only the savepoint, logs short run id plus fixed `create_failed`, and returns false.
- A replay of the same idempotency key never creates a second report because `run_id` is the primary key and report creation only occurs on the original accepted transaction.
- `record_report_stage` validates the finite stage, opens its own short session, and only moves forward according to `REPORT_STAGES`; repeated or older stages are no-ops. Every exception is caught outside a nested savepoint and never propagates into generation.
- Stage and preview callbacks are no-ops for `suppressed`/`failed` delivery rows; disabling the feature cannot be undone by a late generation or preview callback.
- `sync_terminal_report` is called inside the transaction that makes a run terminal. If disabled, suppress any unsent row. If enabled and terminal:
  - failed/cancelled -> `finish_state=pending`;
  - completed with assistant snapshot -> `finish_state=waiting_preview`, `preview_deadline_at=now+5 minutes`, `last_stage=snapshot` unless already later;
  - completed edit without a snapshot -> `finish_state=pending`;
  - completed build without a snapshot is not possible after `finalize_generation_run` classifies it as failed.
- `mark_snapshot_preview_ready` joins `Message.snapshot_id -> GenerationRun.assistant_message_id -> GenerationTelegramReport.run_id`, clears `preview_error_code`, advances `last_stage` to `preview`, and leaves `warning_sent` eligible for exactly one late photo.
- `mark_snapshot_preview_failed` stores only one of the fixed codes `snapshot_missing`, `source_missing`, `container_unreachable`, `render_failed`, `upload_failed`; it does not change the generation or snapshot.
- `suppress_pending_reports` atomically sets every non-sent start/finish state to `suppressed`, clears leases/retry times, and does not alter already sent chat messages.

- [ ] Add failing service tests: disabled flag; build/edit rows; clarify exclusion; duplicate/replay suppression; nested-savepoint failure isolation; forward-only stages; completed snapshot wait; completed no-snapshot edit; failed; cancelled; preview ready/failure; late preview after warning; suppress-pending; cascade deletion.
- [ ] Run and confirm failures:

```bash
cd apps/api
uv run pytest -q tests/test_generation_telegram_reports.py
```

- [ ] Implement the service with SQLAlchemy `select`/`update` statements and short independent sessions only where the caller does not already own a transaction.
- [ ] Rerun tests and static checks:

```bash
cd apps/api
uv run pytest -q tests/test_generation_telegram_reports.py
uv run ruff check src/omnia_api/services/generation_telegram_reports.py tests/test_generation_telegram_reports.py
uv run mypy src/omnia_api/services/generation_telegram_reports.py
```

- [ ] Commit the state service:

```bash
git add apps/api/src/omnia_api/services/generation_telegram_reports.py apps/api/tests/test_generation_telegram_reports.py
git commit -m "feat(api): manage generation report lifecycle"
```

---

## Task 4: Wire acceptance, terminal state, and normalized stages into generation

**Files:**

- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Modify: `apps/api/src/omnia_api/services/generation_runs.py`
- Extend: `apps/api/tests/test_generation_runs.py`
- Extend: `apps/api/tests/test_generation_telegram_reports.py`

### Transactional integration points

Wire `create_report_for_run` in `post_prompt` after `turn_mode`, `user_message_id`, `assistant_message_id`, and `response_mode` are set, but before the existing outer `session.commit()`. Pass `settings.dev_generation_telegram_reports`. Do not create a row on the replay branches.

Call `sync_terminal_report` before commit in every existing terminal transition:

1. the active-run lifecycle self-heal in `reserve_generation_run`;
2. `_recover_interrupted_generation_runs`;
3. `set_generation_run_status` for `failed`, `completed`, or `cancelled`;
4. `_finalize_generation_run`;
5. `_finalize_cancelled_generation` in `messages.py`.

Keep the observer update in a nested savepoint and catch/log only a fixed local code. A report failure must not prevent project-memory compilation, generation finalization, cancellation, or commit.

### Stage integration points

Use a tiny local helper that awaits `record_report_stage` but catches all observer failures. Record only transitions, never chunks:

- `routing` at the beginning of `_process_prompt` after the durable run becomes `running`;
- `director` when the freeform art-director pass starts;
- `writer` immediately before the selected writer/agent/multipass generation engine starts, including agentic and surgical edit paths;
- `images` at the existing `_emit_stage("images", "start")` boundary;
- `acceptance` immediately before acceptance/quality gates start;
- `snapshot` immediately before each snapshot transaction in both agentic and standard paths;
- `preview` immediately after each successful `enqueue_preview` call, including repair snapshots.

The stage helper must not publish Telegram messages. It only updates PostgreSQL observer state.

- [ ] Add failing endpoint/lifecycle tests showing one row is committed with an accepted build/edit, no row is created for clarify/text turns or replay, and report creation failure still returns HTTP 202 with the generation rows committed.
- [ ] Add failing terminal tests for normal success, build-without-snapshot failure, exception failure, cancellation, startup recovery, and active-run self-heal. Assert each run and report terminal state commit together.
- [ ] Add stage-wiring tests by monkeypatching `record_report_stage`, driving representative build/edit branches, and asserting the normalized order without checking every token/progress event.
- [ ] Run the failing suites:

```bash
cd apps/api
uv run pytest -q tests/test_generation_runs.py tests/test_generation_telegram_reports.py
```

- [ ] Implement acceptance, terminal, and stage hooks. Reuse the existing `settings` object in `post_prompt`; do not call Telegram or MinIO from this code path.
- [ ] Rerun focused regression and static checks:

```bash
cd apps/api
uv run pytest -q tests/test_generation_runs.py tests/test_generation_telegram_reports.py tests/test_messages_result_type.py tests/test_agent_progress.py
uv run ruff check src/omnia_api/routers/messages.py src/omnia_api/services/generation_runs.py tests/test_generation_runs.py tests/test_generation_telegram_reports.py
uv run mypy src/omnia_api/services/generation_runs.py
```

- [ ] Commit generation integration:

```bash
git add apps/api/src/omnia_api/routers/messages.py apps/api/src/omnia_api/services/generation_runs.py apps/api/tests/test_generation_runs.py apps/api/tests/test_generation_telegram_reports.py
git commit -m "feat(api): observe every build and edit lifecycle"
```

---

## Task 5: Connect the existing preview renderer to durable report state

**Files:**

- Modify: `apps/api/src/omnia_api/workers/preview.py`
- Create: `apps/api/tests/test_generation_telegram_preview.py`

### Preview rules

- Keep `render_preview` and `_render_async` as the only PNG production path.
- In the transaction that updates `Snapshot.preview_key`, call `mark_snapshot_preview_ready(session, sid)` before commit. Publishing `preview.ready` remains after commit.
- Convert current silent early exits into fixed observer outcomes without changing their existing generation/preview semantics:
  - missing non-container `index.html` -> `source_missing`;
  - unresolved live container URL -> `container_unreachable`;
  - render/browser failure -> `render_failed`;
  - MinIO upload failure -> `upload_failed`.
- If the snapshot row itself is absent, return as today because no report can be resolved. Do not create an orphan report.
- Observer updates are fail-soft. If they fail, preview upload/event behavior remains unchanged.
- Do not send Telegram from the preview worker. Do not expose a preview URL or MinIO key to the delivery state.

- [ ] Write failing tests using fake repo files, fake Playwright/MinIO, and a real test database: successful render advances the matching report and preserves the PNG key; each early-exit/error stores only the fixed preview code; an observer update exception does not fail a successful preview.
- [ ] Run and confirm failures:

```bash
cd apps/api
uv run pytest -q tests/test_generation_telegram_preview.py tests/test_render_settle.py
```

- [ ] Implement preview callbacks and normalized outcomes.
- [ ] Rerun focused tests plus existing preview tests:

```bash
cd apps/api
uv run pytest -q tests/test_generation_telegram_preview.py tests/test_render_settle.py tests/test_generation_runs.py
uv run ruff check src/omnia_api/workers/preview.py tests/test_generation_telegram_preview.py
```

- [ ] Commit preview integration:

```bash
git add apps/api/src/omnia_api/workers/preview.py apps/api/tests/test_generation_telegram_preview.py
git commit -m "feat(worker): expose preview results to generation reports"
```

---

## Task 6: Implement the dedicated durable delivery worker

**Files:**

- Create: `apps/api/src/omnia_api/workers/generation_reports.py`
- Create: `apps/api/tests/test_generation_report_worker.py`

### Worker contract

Use these fixed operational constants unless a test proves a smaller safe value is required:

```python
POLL_SECONDS = 2.0
LEASE_SECONDS = 45
MAX_ATTEMPTS = 8
BACKOFF_SECONDS = (5, 15, 60, 180, 300, 600, 900, 900)
PREVIEW_WAIT_SECONDS = 300
```

Implement short claim and persistence transactions around external I/O:

1. `reconcile_waiting_previews(now)` joins the assistant snapshot and changes
   `waiting_preview` to `pending` only when a `preview_key` exists or the deadline
   expires. It leaves `warning_sent` untouched until a late `preview_key` exists.
2. `claim_due_report(now)` selects one externally deliverable event using
   `FOR UPDATE SKIP LOCKED`, prioritizes unsent starts over finishes, reclaims
   expired `sending` leases, increments only the selected event attempt, sets
   its state to `sending`, and commits a 45-second lease. Merely waiting for a
   preview does not consume an attempt.
3. Load the current `GenerationRun`, exact user `Message`, assistant `Message`, `Project`, and current result `Snapshot` after the claim. Missing/malformed source rows become fixed permanent local codes; never improvise prompt or screenshot data.
4. Recheck `dev_generation_telegram_reports` immediately before each external call. If false, suppress remaining work without contacting Telegram.
5. For a start event:
   - build the start payload from `user_message_id -> Message.content`;
   - send `sendMessage` only when `start_message_id` is absent;
   - persist `start_message_id` in a separate transaction immediately after Telegram acknowledges it;
   - if a prompt document exists, send it as a reply; an expired lease with a durable message id resumes at the document step rather than duplicating the start;
   - mark `start_state=sent` only after the required text/document sequence finishes.
6. A finish is claimable only when `start_state=sent` and `start_message_id` is durable.
7. For a failed/cancelled/no-snapshot edit, send one text reply and mark `sent`.
8. For a completed run with a snapshot:
   - before `preview_deadline_at`, reconciliation leaves the row in `waiting_preview` and no delivery claim is created;
   - after the deadline, send one warning and mark `warning_sent`;
   - when `preview_key` exists, read bytes from the internal MinIO previews bucket using `get_minio_client().get_object`, always close/release the response, send one photo reply, and mark `sent`;
   - if state was `warning_sent`, caption the one photo `Preview появился позже`.
9. On retryable errors, set the event back to its pending state, store only a fixed code, clear the lease, and schedule bounded backoff or bounded Telegram `retry_after`.
10. On success, clear the lease, retry timestamp, and prior delivery error code.
11. On a permanent error or exhausted attempts, mark that event `failed`. A failed start leaves finish non-claimable for operator replay instead of sending an unthreaded result.
12. `run_forever()` creates an async engine/session factory, suppresses pending rows while disabled, polls forever, and catches errors per cycle. `main()` uses `asyncio.run(run_forever())`.

Use structured log events `start_sent`, `finish_sent`, `preview_warning`, `retry`, `failed`, `suppressed`; include only short run id, event, attempt, and fixed code.

- [ ] Write failing database-backed tests for claim ordering, `SKIP LOCKED` duplicate suppression, expired lease recovery, start-message resume for long prompt, start-before-finish threading, success text, success photo using exact MinIO bytes, preview wait, timeout warning, one late photo, cancellation, sanitized failure, 429/5xx/network retry, bounded retry exhaustion, permanent config/4xx failure, worker restart, Redis absence, kill-switch suppression, missing snapshot, and malformed Telegram response.
- [ ] Add a log-capture test that seeds a bot token, prompt, email, signed URL, Telegram response body, MinIO key, and DSN, then asserts none appears in logs or `last_delivery_error_code`.
- [ ] Run and observe failures:

```bash
cd apps/api
uv run pytest -q tests/test_generation_report_worker.py
```

- [ ] Implement the worker. Inject `TelegramBotClient`, clock, sleeper, and MinIO loader into cycle-level functions so unit tests remain deterministic and offline.
- [ ] Rerun worker, delivery, state, and static tests:

```bash
cd apps/api
uv run pytest -q tests/test_generation_report_worker.py tests/test_generation_telegram_delivery.py tests/test_generation_telegram_reports.py tests/test_generation_telegram_preview.py
uv run ruff check src/omnia_api/workers/generation_reports.py tests/test_generation_report_worker.py
uv run mypy src/omnia_api/workers/generation_reports.py
```

- [ ] Commit the worker:

```bash
git add apps/api/src/omnia_api/workers/generation_reports.py apps/api/tests/test_generation_report_worker.py
git commit -m "feat(worker): deliver durable generation reports"
```

---

## Task 7: Add Compose wiring and secure runtime configuration handling

**Files:**

- Modify: `apps/llm-gateway/deploy/full/docker-compose.yml`
- Modify: `infra/.env.example`
- Modify: `infra/release/update-env-value.sh`
- Modify: `infra/release/test-release-tools.sh`
- Modify: `infra/release/test-compose-policy.sh`
- Create: `apps/api/tests/test_generation_report_compose.py`

### Compose service

Add `generation-report-worker` with:

```yaml
image: ${API_IMAGE:-omnia-api:prod}
container_name: omnia-prod-generation-report-worker
command: ["/app/.venv/bin/python", "-m", "omnia_api.workers.generation_reports"]
depends_on:
  api:
    condition: service_healthy
  postgres:
    condition: service_healthy
  minio-init:
    condition: service_completed_successfully
environment:
  ENV: prod
  LOG_LEVEL: ${LOG_LEVEL:-INFO}
  OMNIA_RELEASE_SHA: ${OMNIA_RELEASE_SHA:-unknown}
  DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-omnia}:${POSTGRES_PASSWORD:-omnia}@postgres:5432/${POSTGRES_DB:-omnia}
  MINIO_ENDPOINT: minio:9000
  MINIO_ACCESS_KEY: ${MINIO_ROOT_USER:-omnia}
  MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD:-omnia-minio-secret-please-change}
  MINIO_BUCKET_PREVIEWS: ${MINIO_BUCKET_PREVIEWS:-previews}
  DEV_GENERATION_TELEGRAM_REPORTS: ${DEV_GENERATION_TELEGRAM_REPORTS:-false}
  TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}
  TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID:-0}
restart: unless-stopped
networks:
  - omnia-prod
```

Also pass only `DEV_GENERATION_TELEGRAM_REPORTS` to `api` and the existing `worker` preview process. Do not pass the Telegram token/chat id to web, API, RQ preview worker, gateway, or orchestrator. The dedicated worker alone receives Telegram credentials.

Add disabled/empty examples to `infra/.env.example` without real values.

### Secret-safe updater

Extend `update-env-value.sh` so the third argument may be exactly `-`, in which case it reads one line from stdin and never prints the value. Retain the existing key validation, one-line validation, regular-file/symlink protection, mode/owner preservation, atomic replace, and key-only success output. This permits an operator to inject a rotated token without placing it in argv or shell history.

- [ ] Write failing tests that rendered Compose has the new service, fixed command, no Redis dependency, API-image reuse, default false flag, credentials only on the dedicated worker, disabled chat sentinel `0`, and a configured negative chat id accepted as a string. Assert existing api/worker defaults remain unchanged.
- [ ] Extend release-tool tests with stdin secret update, permission preservation, duplicate-key replacement, and output/log assertions proving the secret value is absent.
- [ ] Run the expected failures:

```bash
cd apps/api
uv run pytest -q tests/test_generation_report_compose.py
cd ../..
bash infra/release/test-release-tools.sh
bash infra/release/test-compose-policy.sh
```

- [ ] Implement Compose/env/updater changes. Update `test-compose-policy.sh` to assert the flag defaults false on `api`, `worker`, and `generation-report-worker`, and that Telegram credential fields are empty only on the dedicated worker in the blank policy render.
- [ ] Rerun focused checks:

```bash
cd apps/api
uv run pytest -q tests/test_generation_report_compose.py
cd ../..
bash infra/release/test-release-tools.sh
bash infra/release/test-compose-policy.sh
docker compose -f apps/llm-gateway/deploy/full/docker-compose.yml config --quiet
```

- [ ] Commit runtime wiring:

```bash
git add apps/llm-gateway/deploy/full/docker-compose.yml infra/.env.example infra/release/update-env-value.sh infra/release/test-release-tools.sh infra/release/test-compose-policy.sh apps/api/tests/test_generation_report_compose.py
git commit -m "feat(ops): run generation Telegram report worker"
```

---

## Task 8: Extend release/rollback safety and operator acceptance docs

**Files:**

- Modify: `infra/release/README.md`
- Modify: `infra/release/local-release-gate.sh`
- Modify: `infra/release/test-release-tools.sh`
- Create: `apps/api/scripts/dev_generation_telegram_acceptance.py`
- Create: `apps/api/tests/test_dev_generation_telegram_acceptance.py`

### Release and rollback changes

Update the existing release runbook rather than adding a competing deployment path:

- Record whether `omnia-prod-generation-report-worker` exists before rollout. If present, capture its image id and assert it equals the API image id. If absent on the first rollout, record `absent` without failing the backup step.
- Prepare `DEV_GENERATION_TELEGRAM_REPORTS`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` in the candidate full env before rendering Compose. Use the updater's stdin mode for both Telegram values. Never print, `tee`, `set -x`, or pass them as command arguments.
- Render the candidate Compose and assert the report worker receives `true`, a non-empty token, the fixed negative chat id, the exact release SHA, database settings, and MinIO settings. Assertions may test presence/shape but must not emit secret values.
- Roll out in dependency order: `api` (migration), existing `worker`, `generation-report-worker`, then web/orchestrator. Assert the report-worker container is running and uses the exact revision-tagged API image.
- Rollback is compatible with a target revision that predates the service. Before checking out the rollback SHA, stop/remove `omnia-prod-generation-report-worker`; restore the protected env backup; then continue the existing API/worker/web/orchestrator rollback. Keep migration `0047` applied because it is additive; never downgrade it.
- Add the new focused tests to the API test step in `local-release-gate.sh`. The API image build already covers the report worker because Compose reuses the same image.

### Acceptance script

Create an operator script that is read-only with respect to Telegram credentials and produces a redacted JSON summary. It must:

- accept API base URL and the existing protected canary credentials through environment variables;
- reuse the existing production canary client helpers where practical;
- create a disposable project, run one build and one edit, optionally cancel a third run when `DEV_TELEGRAM_ACCEPTANCE_CANCEL=true`, and always delete the disposable project;
- emit only run ids, modes, terminal statuses, snapshot/preview booleans, and timestamps;
- never emit prompt text, account email, password, cookie, token, chat id, preview URL, source, or response body;
- return non-zero if a requested generation does not reach the expected terminal state.

The human Telegram verification remains explicit: confirm two start messages and their threaded finish/photo replies in the configured group. The script cannot treat Telegram chat history as a queue or query it with bot updates.

- [ ] Write failing tests for acceptance-summary redaction, cleanup-on-failure, build/edit/cancel sequencing, and non-zero exit on an unexpected generation state.
- [ ] Extend release-tool tests to simulate a rollback target without the new service definition and prove the documented cleanup/resume steps do not require checkout-local helpers after rollback begins.
- [ ] Run failing checks:

```bash
cd apps/api
uv run pytest -q tests/test_dev_generation_telegram_acceptance.py
cd ../..
bash infra/release/test-release-tools.sh
```

- [ ] Implement the acceptance script and runbook/gate updates. Include a pre-public-release subsection with the exact zero-active-generation check, flag disable, container recreation, and proof that a test build creates no report row.
- [ ] Rerun docs/tool/script tests and shell syntax:

```bash
cd apps/api
uv run pytest -q tests/test_dev_generation_telegram_acceptance.py tests/test_production_canary.py
cd ../..
bash -n infra/release/update-env-value.sh infra/release/test-release-tools.sh infra/release/local-release-gate.sh
bash infra/release/test-release-tools.sh
bash infra/release/test-compose-policy.sh
```

- [ ] Commit operations and acceptance support:

```bash
git add infra/release/README.md infra/release/local-release-gate.sh infra/release/test-release-tools.sh apps/api/scripts/dev_generation_telegram_acceptance.py apps/api/tests/test_dev_generation_telegram_acceptance.py
git commit -m "docs(ops): add generation report release procedure"
```

---

## Task 9: Run complete verification, review, merge, and deploy dark-to-live

**Files:**

- Verify all files changed in Tasks 1–8
- Do not create ad-hoc production scripts or plaintext secret files

### Local verification and review

- [ ] Scan for forbidden placeholders and accidental secret material before tests:

```bash
! git diff --name-only -z origin/main...HEAD | xargs -0 rg -n 'TO''DO|TB''D|PLACE''HOLDER'
! rg -n '[0-9]{8,12}:[A-Za-z0-9_-]{30,}' apps/api/src/omnia_api apps/api/tests apps/api/migrations infra docs/superpowers
git diff --check
```

The token-specific scan must return no match. Existing unrelated unfinished
markers outside changed files may be reported but are not modified as part of
this feature.

- [ ] Run the full focused suite:

```bash
cd apps/api
uv run pytest -q \
  tests/test_secret_safety.py \
  tests/test_generation_telegram_delivery.py \
  tests/test_generation_telegram_reports.py \
  tests/test_generation_runs.py \
  tests/test_generation_telegram_preview.py \
  tests/test_generation_report_worker.py \
  tests/test_generation_report_compose.py \
  tests/test_dev_generation_telegram_acceptance.py \
  tests/test_production_canary.py \
  tests/test_render_settle.py
uv run ruff check .
uv run mypy src
cd ../..
bash infra/release/test-release-tools.sh
bash infra/release/test-compose-policy.sh
```

- [ ] Run the full repository release gate on local disposable databases exactly as documented:

```bash
export EXPECTED_RELEASE_SHA="$(git rev-parse HEAD)"
export DATABASE_URL='postgresql+asyncpg://localhost/omnia_release_gate'
export DATABASE_TEST_URL='postgresql+asyncpg://localhost/omnia_release_gate_test'
export JWT_SECRET='local-release-gate-secret-32-bytes-minimum'
bash infra/release/local-release-gate.sh
```

- [ ] Use `superpowers:requesting-code-review` and resolve all Critical/Important findings with `superpowers:receiving-code-review`. Repeat focused tests and `git diff --check` after fixes.
- [ ] Confirm a clean worktree, review every commit, push the feature branch, open a PR, wait for required CI, and merge to protected `main`:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
git push -u origin codex/generation-telegram-observer-design
gh pr create --base main --head codex/generation-telegram-observer-design --fill
gh pr checks --watch
```

Do not merge with failing or pending required checks. Record the exact merge SHA after merge.

### Production-style internal development rollout

- [ ] Fetch the exact merged `main`, rerun the local release gate at that SHA, and present the owner with the exact command required by section 1 of `infra/release/README.md`:

```text
Deploy RELEASE_SHA=0123456789abcdef0123456789abcdef01234567; rollback to ROLLBACK_SHA=89abcdef0123456789abcdef0123456789abcdef.
```

Replace the example revisions with the two measured exact revisions before
presenting the command. Do not SSH, mutate runtime env, restart containers, run
migrations, or run the paid canary until that exact confirmation is received.

- [ ] After confirmation, follow the release runbook without shortcuts: capture live state/backups, prove zero active generations, build revision-tagged images, prepare candidate env, render/validate Compose, persist rollback manifest, roll out API/migration then workers, verify exact health identities, run smoke and generation canary.
- [ ] Rotate the bot token in BotFather. Update the protected GitHub `production` environment secret used by the daily canary and, on the production host with shell tracing disabled, inject the same replacement token plus fixed group id into the candidate env using stdin mode. Set `DEV_GENERATION_TELEGRAM_REPORTS=true` only for the current internal development period. Do not reuse or print the token pasted in chat.
- [ ] Verify the report worker before test generations:

```bash
test "$(docker inspect -f '{{.State.Running}}' omnia-prod-generation-report-worker)" = true
docker inspect -f '{{.Image}}' omnia-prod-generation-report-worker > /tmp/omnia-report-worker-image-id
docker inspect -f '{{.Image}}' omnia-prod-api > /tmp/omnia-api-image-id
cmp -s /tmp/omnia-report-worker-image-id /tmp/omnia-api-image-id
rm -f /tmp/omnia-report-worker-image-id /tmp/omnia-api-image-id
```

- [ ] Run the live acceptance script for build and edit using protected canary credentials, then inspect the team group for:
  - one yellow start per run;
  - the exact user-authored message after credential redaction;
  - a finish reply threaded to the correct start;
  - the preview PNG for snapshot-producing runs;
  - no account identity, source, effective prompt, token, signed URL, or raw response body.
- [ ] Run cancellation acceptance. In a pre-announced internal-development maintenance window with zero active generations, temporarily make only the disposable canary's gateway call fail, restore the gateway immediately, then verify the sanitized failure stage/error. Do not run this fault injection after public launch or while another generation is active.
- [ ] Simulate delivery outage without changing generation: stop only `generation-report-worker`, complete a disposable build, restart it, and verify the persisted start/finish backlog reconciles. Do not stop API, RQ preview worker, Postgres, Redis, or MinIO for this check.
- [ ] Query counts/states only; do not select prompt or error text:

```sql
SELECT start_state, finish_state, count(*)
FROM generation_telegram_reports
GROUP BY start_state, finish_state
ORDER BY start_state, finish_state;
```

- [ ] Preserve release evidence and report the exact deployed SHA, health/canary results, observer acceptance results, known Telegram duplicate window, and reminder that the feature is development-only.

### Mandatory pre-public-release disable proof

Before the product is released publicly:

- [ ] Prove zero active generations with the runbook query.
- [ ] Set `DEV_GENERATION_TELEGRAM_REPORTS=false` in a protected candidate env, render Compose and assert false for `api`, `worker`, and `generation-report-worker`, then atomically replace the live env.
- [ ] Recreate those three containers at the approved release SHA. The report worker remains running but suppresses pending rows and performs no Telegram calls.
- [ ] Run one disposable build/edit canary and prove no new `generation_telegram_reports` row exists for its run ids and no Telegram message appears.
- [ ] Keep the default false in source and record the disable evidence in the release record. Re-enabling after public release requires a separate privacy/operations review.

---

## Definition of Done

- Every accepted development build/edit has one durable report row; clarify/text/replay requests do not.
- Start delivery contains only project label, mode, short run id, and exact redacted user-authored message; long text is fully preserved as an in-memory UTF-8 document.
- Success uses the existing preview PNG bytes; failure/cancel/preview timeout is threaded and sanitized; late preview is sent at most once.
- Telegram/MinIO/report-database faults cannot alter generation outcome or latency-critical request behavior.
- Worker restart, expired lease, Telegram outage, Redis reset, and missing callback paths reconcile from PostgreSQL.
- Token, account identity, system/effective prompts, source, signed URLs, response bodies, and raw exceptions do not appear in Telegram diagnostics, database delivery errors, logs, source, docs, or test artifacts.
- Focused tests, full Ruff/mypy/API tests, release tools, Compose policy, local release gate, required CI, code review, and live acceptance all pass.
- The exact merged SHA is deployed through the documented release/rollback procedure only after exact SHA confirmation.
- A dated operational reminder exists to set `DEV_GENERATION_TELEGRAM_REPORTS=false` and prove no row/send before public release.
