# Working integration runtime

Goal: make existing MAX integrations produce verifiable results without leaking credentials, reporting false success, or duplicating ambiguous external writes.
Architecture: keep the encrypted business-scoped vault and project bindings. Use a common locked credential loader, validated provider responses, a durable CRM operation record, and a capability contract consumed by both Studio and the generation agent. Existing unsupported providers remain unavailable until their adapters and account-level acceptance tests exist.

Authorized design: user approved the Integration Hub flow described in the preceding conversation (connect credentials/OAuth, select settings, AI uses managed methods, verify and publish). This plan executes that request in the existing isolated project checkout. No new provider subscriptions, live financial transactions, or customer messages are implicit test actions.

- [x] Credentials: reproduce expired-token verification, concurrent refresh, and temporary provider outages using real PostgreSQL plus controlled HTTP boundary; centralize row-locked refresh and retain active state on temporary failure.
- [x] Payments/provider contracts: reproduce cross-user status reads, shared-shop key collisions, malformed/HTTP-200 error responses; scope idempotency and enforce metadata ownership and strict response contracts. Payment completion must be determined server-side, not by browser redirect.
- [x] CRM writes: persist a scoped operation key, request digest, dispatch state and result before external effects. Replay confirmed successes; reject changed payloads and ambiguous/unfinished operations. Test concurrent replay and accepted-request/lost-response failure.
- [x] Catalog and runtime: fix stock/menu semantics and capability declarations; expose only implemented operations, preserve provider errors as actionable UI failures, and ensure SDK and trusted runtime use matching routes.
- [x] AI flow: provide secretless connected capabilities to generation; make Studio offer a concrete integration implementation prompt after connecting, without silently launching a generation or claiming credentials alone built the feature.
- [ ] Acceptance: execute tests/lint/types/migration checks, independent review, commit/push/CI, production deployment and health. Verify real provider credentials read-only where available; distinguish controlled contract tests from real account transactions. Record any missing account prerequisites explicitly.

Verified locally: 114 API tests before final review fixes; final targeted regressions recorded in delivery evidence. Web 231 tests; orchestrator 1075 tests plus new draft-overlay regression; API and orchestrator strict types; migration 0057→0058→0057→0058 on disposable PostgreSQL. Final review found and fixed reconnect payment-key instability, definitive CRM rejection retry, and missing draft-core overlay. Managed kit revision 15 ships the updated SDK on the next managed refresh. Production public core must be rebuilt and pinned; publication recovery reconciles existing live cores.

## Correction: built-in text AI through LLMGW

The owner confirmed that mini-app text AI must use the existing LLMGW route.
AITUNNEL credentials are not required for text AI. Legacy records remain stored
but are hidden and cannot be connected, bound, verified or used for inference.
Media providers in the gateway are separate from this text feature.

- [x] Add owner-only platform AI enable/disable; database default is disabled.
- [x] Route MAX-authenticated, rate-limited calls through the internal gateway
  with the Project owner as billing identity and server-owned model/token limits.
- [x] Require billing (`free=false`, `require_billing=true`) and fail closed on
  wallet lookup/debit failures, including before serving cached answers.
- [x] Show built-in AI, balance cost and explicit enable/feature generation controls
  without an integration key or a fabricated external connection record.
- [x] Test disabled legacy bindings, ownership, malformed input, safe errors,
  billing identity and gateway failure behavior; test migration 0059 up/down/up.
- [ ] Deploy gateway before API and verify a published app through LLMGW.
