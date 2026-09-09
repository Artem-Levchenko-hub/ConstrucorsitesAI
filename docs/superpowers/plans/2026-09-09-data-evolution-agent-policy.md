# English data-evolution policy: implementation and delivery

Approved by the owner on 2026-09-09. Architecture: [client versioning](../specs/2026-09-09-client-versioning-design.md).
Baseline: `69520471fb09f54c2e012f6a56028c5e93f89295`, tracked checkout clean, `HEAD == origin/main` after fetch.

## Deliverable

Ship one shared English policy to MAX code-writing agents, including Project Cell,
legacy generation, edits, continuation and repair. Include concrete examples of
optional fields, labels, type/meaning changes, partial writes and dependent rows.
Keep the requested application/response language and existing execution rights.

This is the first implemented prerequisite of the accepted versioning design.
It does not implement a migration controller, restrict PostgreSQL credentials,
certify model compliance or enable MAX restore. The existing restore guard stays.
No real customer/model generation is launched as part of this package.

## 1. Regression tests before code

- Add `apps/api/tests/test_max_data_evolution.py`.
- Exercise actual one-shot build/edit builders, imported MAX edits, model tiers,
  native/text agent composition and portable selection with a real awaited source
  snapshot. The policy must survive replacement of the legacy guide exactly once.
- Exercise autoheal with fake external Redis/compiler/LLM boundaries, capturing the
  actual outgoing system prompt. Preserve the non-MAX path.
- Extend `test_browser_container_contract.py` to verify template context reaches
  background repair. No live DB/model/queue is used in these tests.
- RED: run the new focused tests on the unchanged implementation. These tests prove
  routing/composition only, not that generated SQL is safe.

## 2. Implement shared English instructions

- `services/max_data_evolution.py`: policy and MAX guide composition after provider
  selection. No SQL scanner, new permission claim or invented migration API.
- `routers/messages.py`: use the shared guide; preserve it in edit auto-repair.
- `services/prompt_builder.py`: include policy in MAX code prompts, including
  imported edits; omit from prose-only art direction and non-MAX projects.
- `services/autoheal.py`, `routers/runtime.py`: pass template context to repair.
- `services/portable_cell_contract.py`, `services/max_project_kit.py`: clarify that
  development DB access does not authorize destructive changes or safe restore.
- `docs/04-generation-rules.md`: document the operational source and limitations.

## 3. Verify and review

- Focused new tests plus portable, MAX edit, agent native/text, runtime consumers.
- API Ruff, mypy and full locked API regression suite on disposable services.
- Independent read-only Astra review of the complete diff; resolve findings.
- Separate adversarial read-only review of examples: old writes must preserve new
  fields; ambiguous conversions must not invent values; no compliance guarantee.
- Inspect full diff, paths, lockfiles and secrets. No schema migration is included.

## 4. Commit, push, deploy and verify

- One serial owner; commit intended files only, push to configured `origin/main`.
- Verify pushed SHA and CI. Build API image from the exact pushed source archive.
- Production: `/opt/omnia`, compose project `full`,
  `apps/llm-gateway/deploy/full/docker-compose.yml`.
- Recheck component revisions, dirty documents and active generations/operations/
  leases. Restart API, worker and generation-worker only after the activity guard
  and temporary write gate; preserve web, orchestrator and business-data volumes.
- Confirm container image/revision/health, policy composition in the deployed
  image without a model call, HTTP smoke and removal of the temporary write gate.
- Record exact delivery evidence and residual risks in a verification document.

## Next implementation packages

1. Trusted migration controller and non-owner published-app PostgreSQL role;
   real permission tests, old credentials/connections fenced, legacy opt-in.
2. Enforced read/write/delete contracts, actual schema evidence and immutable
   release artifacts; isolated compatibility tests with real business actions.
3. Durable restore preparation/activation/reconciliation, current-draft versus
   public-release UI, idempotency and a full surname persistence scenario.
4. Public traffic/jobs/old clients, adapted historical releases, separate tested
   backup recovery and retention/capacity policy.

These remain separate delivery gates, not features provided by the prompt block.
