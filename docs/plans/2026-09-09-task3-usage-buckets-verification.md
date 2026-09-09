# Task3-adjacent R: one usage-bucket initializer

BASE `fd9d2e10d959a5bf08440082738fa175dddd2bcd`. Implementation, independent
review and delivery use GPT-6 Astra. The preceding Task11 delivery and public
H149 update are complete before this package begins.

## Change and preserved behavior

`get_max_usage` uses one local typed dictionary for zero values, with separate
copies for the template stage and each newly encountered stage. Two duplicated
initializers are removed. The existing endpoint remains the sole owner; no new
function, import, dependency, module or configuration mode is introduced.

Production source: **672 to658 lines; 26,769 to26,449 UTF-8 bytes** — a reduction
of **14 lines and320 bytes**. Test90 lines and fixture123 lines are measured
separately. All code before initialization, including SQL and filters, all
accumulation statements and the response construction are byte-identical.

Three queries, row order, latest-run filtering, all totals, stage order, template
presence, unknown-stage handling and errors remain as before. Each dictionary
copy has independent scalar counters; real template rows accumulate normally.
The existing `0.1+0.2` stage value remains `0.30000000000000004`. Query loading,
arithmetic and precision are unchanged; the SQL aggregation part of Task3 remains
open. Source reduction alone does not establish a runtime speed improvement.

## Verification

- BEFORE/AFTER: the same **8 tests pass**, with six complete input/expected JSON
  fixtures plus404/409 early rejections. Expected results were fixed before the
  source edit. Tests use actual endpoint/Pydantic serialization, real Usage
  models with Decimal values and a fake session.
- Cases cover empty data with/without run, latest/old/NULL runs, unknown/NULL
  stage, all six stages including real template rows, independent stage totals,
  cache counters/retries and forward/reverse tied-timestamp row order.
- **75 tests pass in the combined focused/neighboring run, including the eight
  new cases**; Ruff over all src/tests passes; full mypy:
  **276 source files clean**. Diff check and byte-comparison checks pass.
- The first neighboring run lacked the required dummy `JWT_SECRET` and had
  ten Settings-validation failures. The identical suite passed after correcting
  only the test-process environment; original failure output is retained.
- Exact current production API image
  `sha256:47ac83c1f70ee7351812128fcfc56869e8bdd020fe3143ff45207c4fb2cf4664`
  passes the same eight cases in an ephemeral network-none/read-only container,
  using stdlib mocks and the actual installed endpoint, without pytest or DB.
  BEFORE record: `/opt/omnia-runtime/releases/task3-usage-buckets-baseline-fd9d2e10`.

Frozen fixture `apps/api/tests/fixtures/max_usage_response.json` SHA256:
`f5cdd57c9e9c95496457ccc9df8f9524904e9b4c4747e45a275569985c7fbb2b`.
Source AFTER SHA256:
`32fa84471a1d1f931169a4804f882b1c614e531c2c417687d03759c312ad5bcd`.
Raw commands/logs, measurement script and standalone image verifier:
`.artifacts/task3-usage-buckets/report.md` and adjacent files.

Reproduce from `apps/api` with Python3.12 and unused-loopback `DATABASE_URL`,
`DATABASE_TEST_URL`, `REDIS_URL`, plus a dummy test-only `JWT_SECRET`:
`python -m pytest tests/test_max_usage_response.py tests/test_max_generation_contract.py tests/test_max_runtime_probe.py tests/test_max_client.py tests/test_max_edit_lock.py tests/test_public_max_credentials.py -o addopts='' -q`;
`python -m ruff check src tests`; `python -m mypy src`.

These local cases do not execute PostgreSQL ordering/ownership filters, real
FastAPI authentication or HTTP error envelopes. Those paths are unchanged and
require the existing full integration gates; no full local DB suite is claimed.
Production test data and live model/user generations are not used.

## Pending delivery

Independent Astra source/tests/fixture/image-verifier review: No findings.
Fresh complete API CI on disposable PostgreSQL/Linux and AFTER exact-image
smoke must finish before deployment. The private delivery helper passed syntax
and independent review.

Independent consumer review permits API-only rollout: this function is an HTTP
endpoint used by `MaxUsageBreakdown`, with no worker callers. Health reports each
dependency's release without requiring equality. Expected running identities:
API at the new revision; workers50565efd, webc2da4f71 and orchestratorb99af471.
Preserve their IDs/StartedAt/Status and dirty documents. Shared `API_IMAGE` and
release metadata alter desired Compose configuration for untouched services;
they must not be recreated as part of this package. The historical uniform-SHA
canary is not weakened or represented as passing this scoped deployment.
