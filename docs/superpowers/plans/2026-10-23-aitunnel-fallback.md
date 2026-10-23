# AI Tunnel Sonnet fallback implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Primary owns integration, Git and deployment.

**Goal:** Continue the same Sonnet 5.5 generation through AI Tunnel when LLMGW explicitly refuses the request before returning an accepted completion.

**Architecture:** Keep one provider call per durable generation attempt. Persist the primary refusal before authorizing a deterministic child attempt; choose its provider from the committed parent. Legacy requests without recovery identities use one internal, sequential backup attempt. Separate upstream expense records and provider-specific prices; charge the successful logical result once.

**Tech Stack:** Existing Python/httpx/PostgreSQL gateway and API; no new dependencies.

**Spec:** User request 2026-10-23 and provider support statement: LLMGW returns HTTP 502 when primary and reserve channels fail; non-200 responses are not charged. Official AI Tunnel catalog and authenticated models endpoint verified Sonnet 5.5. Evidence outside Git contains no credentials.

## Global constraints

- Baseline origin/main and live API/controller: 1008972e3895654568d85665823feac9cb92e2aa.
- Exact backup model claude-sonnet-5.5; do not silently downgrade. Existing image/video credentials stay separate.
- No fallback after partial content/tools, unknown post-dispatch timeout, monetary evidence, or failed receipt commit.
- Only configured official LLMGW chat endpoint and explicit HTTP 502 qualify for the new overload policy; ambiguous errors remain terminal.
- Backup enabled only after verification; keys remain outside Git and diagnostics.
- Full tests, independent review, upstream push, documented production deployment, health and relevant user-path proof required.

## Review focus

- Concurrent/cold continuation must not send an extra paid request or bind a response to the wrong attempt.
- Identical provider request IDs from different providers must not collide.
- Partial tool calls and incomplete stream receipts must not become free retry permission.
- Missing key, backup refusal and unexpected model must fail safely without another fallback.
- AI Tunnel cost must use its actual receipt/tariff and scope, not the LLMGW catalog.

### Task 1: Provider identity, settings and accounting

Owner: offline_backup_verifier. Files: gateway core/config.py, services/provider_calls.py, services/generation_attempts.py, services/pricing.py; API provider_call model and new Alembic migration; production compose/example; focused tests.

- [x] Add failing tests for provider-scoped receipt dedup, invalid provider, terminal refusal reason and AI pricing.
- [x] Add provider_scope='llmgw' compatible arguments; explicit aitunnel scope and separate text secret; migration widens checks without rewriting old migrations.
- [x] New refusal reason upstream_refused; resolve_request_cost accepts provider_scope; AI receipt wins, otherwise exact verified tariff with honest provenance.
- [x] Run focused gateway and PostgreSQL contract gates.

### Task 2: Durable API continuation

Owner: recovery_host_export. Files: API agent_native.py, llm_client.py, generation/provider_attempts.py and recovery reconciliation, focused tests.

Interface: HTTP 503 api_error with details provider_failure_kind=upstream_refused, provider_charge_ambiguous=false, retry_safe=true, retry_policy=llmgw_overload_v1, upstream_http_status=502. Permission follows durable failed primary receipt. Gateway chooses backup from parent SQL, never an untrusted client provider hint.

- [x] Test trusted refusal -> one child; untrusted/ambiguous/partial errors -> no retry; cold recovery uses the same predicate.
- [x] Extend existing bounded retry mechanism, preserve prompt/tools, identity and logical billing.
- [x] Run API native/chat/recovery regression tests.

### Task 3: Gateway failover and real provider contract

Owner: primary. Files: new provider routing/fallback adapter, native/chat/streaming routes and relevant billing scope; focused tests.

- [x] Test explicit primary 502 -> separate backup call -> ordinary successful response and one customer charge; all-provider failure is terminal.
- [x] Validate rejection policy, server-owned parent route, cancellation, partial stream and absent receipt.
- [x] Implement one bounded fallback before accepted output; preserve messages/tools and model, actual provider scope and internal fallback metadata.
- [x] Probe AI Tunnel Sonnet 5.5 tool call, tool result and streaming with minimal synthetic payload; record sanitized model/cost/results.
- [x] Run focused tests followed by required complete gateway/API and PostgreSQL contracts (gateway 1032, API/migrations 232; Linux PostgreSQL 199 passed, four Redis-dependent cases deferred to configured CI).

### Task 4: Review and delivery

- [x] Independent Astra read-only review of entire diff and adjacent contracts; fix concrete findings.
- [ ] Secret/diff sanity, intended-files commit and upstream push; exact revision CI.
- [ ] Configure separate production text secret securely; documented production deploy with migration and gateway, exact SHA.
- [ ] Verify health/revisions and isolated real flow; publish report with evidence, remaining limitations and rollback.
