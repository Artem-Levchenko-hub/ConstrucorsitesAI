# Temporary upstream spending reservations

An official LLMGW HTTP 402 may wrap an OpenRouter refusal with exact metadata
`reason=in_flight_budget_exhausted` and
`limit_source=openrouter_in_flight_budget`. This differs from an exhausted
balance or a request whose own estimate exceeds the entire budget.

The Gateway recognizes only bounded, fully read responses from its configured
official LLMGW POST endpoint. It accepts the direct structured error or one
known LiteLLM wrapper, rejects financial/usage evidence, duplicate JSON fields,
conflicting delays and redirects, and requires a positive `Retry-After` no
greater than one day. Unknown or ordinary payment failures keep their previous
terminal/fallback behavior.

For a recognized refusal, the Gateway commits the failed provider receipt and
closes the attempt with `rate_limit_refused` before returning
`llmgw_inflight_budget_v1`. It keeps the same primary provider rather than
automatically routing this temporary reservation to the backup.

The API waits the full stated delay, within the original call/generation
deadline, then obtains a distinct SQL-verified child attempt of that closed
parent. The model, request content, generation identity and agent/tool position
remain unchanged. Known chat models receive two additional refusal-only wire
slots; ordinary transport retries and one-time fallback retain their existing
caps. Native calls retain their existing bounded retry window. Cancellation,
an insufficient remaining deadline, an unknown financial outcome or an invalid
parent stops before any new provider dispatch. The classic source loop records
its wait in the transcript as “Жду освобождения бюджета модели”.

Executable V3 recoveries retain their persisted deadline, consumed wire slots
and original allowance after upgrade. Recognizing this error does not enroll a
legacy project in executable recovery and does not grant general restart
survival to legacy runs.

A historical `failed` run must never be reopened by changing its database
status. Continue it through normal project admission: a standalone “Попробуй
ещё раз” request can restore the adjacent validated private source checkpoint.
That is a new admitted run in the same project, not a new product from an empty
template. Check source ownership, snapshot baseline, object SHA256 and the
absence of an intervening run; never overwrite accepted business data.

Deployment changes both API and Gateway: use the documented canonical
`infra/release/deploy-prod.sh <full main SHA> --gateway` after exact-revision CI
and release guards. Preserve the configured ordinary generation window
(currently 3300 seconds) and legal/restoration settings. No paid request is
needed to validate the classifier against a retained redacted refusal.

Provider reference:
<https://openrouter.ai/docs/api_reference/limits#in-flight-spending-budget>
