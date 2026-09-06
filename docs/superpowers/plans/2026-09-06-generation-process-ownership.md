# Generation process ownership implementation plan

> Execute with the existing debugging, test-first and review workflow.

Goal: an API crash/restart must not interrupt a MAX generation, lose its dispatch,
change its Cell operation ownership or repeat its effects.

The API commits the existing exact GenerationDispatch and an explicit worker
backend before acknowledging the request. A separate Compose generation-worker
process scans pending dispatches. A PostgreSQL session advisory lock remains held
for the entire execution; claim persistence precedes starting any effects. API
startup and capacity recovery only own API-backed work. Cell operations record
which generation executor created them, including capacity work on other cells.
Worker ownership is independent of the API container and its WebSocket listener.

This slice prevents loss of the executor when the API crashes. It does not replay
an executor that itself crashed: a claimed orphan is explicitly failed until a
complete tool journal can reconcile its unknown effects. No blind re-execution,
no status-only success, no deadline reset. Existing legacy runs retain API ownership.

- [x] Add additive ownership fields/migration and recovery filtering tests.
- [x] Add single-owner durable worker dispatch, orphan handling and process health.
- [x] Route MAX prompts to durable worker; keep other existing flows compatible.
- [x] Verify real database races, queued cancel and API startup during active work.
- [ ] Review, commit/push, deploy using full Compose with active-generation guard.
- [ ] Start an owned real generation and restart only API; prove same run/worker
      continues, then verify completed snapshot and owner preview.
