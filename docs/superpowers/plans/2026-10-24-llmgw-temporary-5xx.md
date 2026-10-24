# LLMGW temporary 5xx fallback implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Primary owns Git and deployment.

**Goal:** Continue the same Sonnet 5.5 generation through AI Tunnel after a confirmed temporary LLMGW refusal, retaining ordinary retry and repair behavior.

**Architecture / accepted design:** Extend the existing durable refusal path to HTTP 500, 502, 503 and 504 from the exact official LLMGW POST endpoint. A bounded, completely read refusal without model output, usage, expense headers or redirects is mandatory. Preserve the old 502 policy reader for retained responses; version the broader policy. Persist the failed primary receipt before one backup child. Existing connection/rate-limit retry budgets and ordering, cancellation, ambiguous-outcome handling, tools/history and settlement remain intact. No blanket 5xx retries, schema changes, credentials, pricing or UI changes. Support's supplied non-200 non-billing statement is provider-specific, not an inference from HTTP alone.

**Tech stack:** Existing Python/httpx gateway and API, PostgreSQL receipts, pytest, Ruff/mypy, canonical production release.

**Ruling during implementation:** A new Sonnet chat/SSE dispatch reserves its ordinary wire allowance plus one refusal-only backup slot. Previously the shared two-wire ceiling could consume that slot on an ordinary connection retry. The ordinary LLMGW retry predicates and total deadline stay unchanged. Historical one/two-wire persisted plans keep their original caps, including the local loop's effective cap; cold recovery never refills a saved budget.

**Ruling after full CI:** CI caught cold reconstruction treating the unused refusal-only slot as another ordinary connection attempt. Reproduce the existing PostgreSQL test unchanged, then pass the unchanged ordinary cap as transient dispatch state. Only a verified same-transport continuation uses `min(saved total, ordinary cap)`; a confirmed provider refusal retains its reserved slot. Preserve stored plans/deadlines and native defaults. Keep the old semantic assertions; derive subsequent fixture frames from their actual immutable saved total. Run the full related PG file and the new exact-SHA CI in parallel after focused checks and independent review, and require both before production deployment.

## One vertical slice

- [x] Add regression tests first: all four statuses across classifier, sync/native/streaming routes and API retained continuation. Observe expected failures.
- [x] Implement the shared status/body policy and its versioned API reader; inspect and simplify immediate collaborators without unrelated cleanup.
- [x] Prove 501/505/unknown 5xx, mixed/partial/oversized output, usage/cost evidence and AI Tunnel refusal never authorize a new fallback. Prove ordinary retry allowances and at-most-one backup remain intact, including old-policy compatibility.
- [x] Run focused tests, full gateway, relevant API/recovery/accounting tests, lint/types and diff/secret sanity. No extra paid canary: existing real-provider acceptance plus synthetic HTTP fault injection covers this classifier-only extension.
- [x] Independent defect-first review, fixes and affected reruns.
- [ ] Update the established GitHub report with verified source results and exact coverage/limits. Commit intended files, non-force push configured origin/main, require exact-SHA full CI.
- [ ] Deploy that pushed SHA through documented production compose/project with gateway rebuild, confirm matching service identities/health, model access and released admission. Record blockers honestly until canonical completion.

## Ownership and rollback

Implementation worker owns gateway/API product source and regression tests; primary owns this plan, report, Git and deployment. Reviewers are read-only. Keep the existing fallback flag as operational disable; preserve receipts and historical policies. Reuse the isolated clean checkout; initial local/upstream/live baseline is 27a3ed43df0892728e163ac31e8d35404d5fc9de.
