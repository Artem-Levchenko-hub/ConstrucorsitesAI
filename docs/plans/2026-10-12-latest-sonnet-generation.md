# Latest Sonnet: generation delivery and acceptance

## Verified baseline

Cartel's five recorded runs on 11 October contain two completed and three failed
runs. Two failures reached fast source checks; one received upstream HTTP 401.
Completed runs took 830.6 and 467.6 seconds from admission request to completion;
their production-build phases took 177.0 and 178.8 seconds. This small sample
does not establish platform-wide reliability or explain the customer's entire
elapsed session. Recorded usage for four runs identifies Gemini Custom Tools.
Build success did not establish readable UI.

On 12 October, a bounded live provider probe of
`~anthropic/claude-sonnet-latest` returned HTTP 200, the same response model ID,
one valid required tool call and `finish_reason=tool_calls` in 3.11 seconds.
This proves the alias/tool protocol, not an application build or the underlying
version behind the provider's mutable alias.

## Change

1. Use the exact provider alias as the orchestration default. Preserve explicit
   role/admin overrides and verify their effective production values.
2. Pass the selected model through native generation, edits and source repair;
   preserve the older native default for direct callers.
3. Register exact routing, vision, actual response identities and pricing in
   Gateway. Prefer provider RUB receipts, then validated catalog quotes. The
   verified latest quote is 349.8078/1749.0389/17.4904 RUB per million fresh
   input/output/cached input tokens. Preserve historical receipts and Sonnet 5.
4. Give MAX build/edit/resume a concise product contract: complete the request,
   use coherent typography and color pairs, preserve existing business data,
   fix actual compiler errors and distinguish source checks from visual proof.

## Anthropic guidance and transport boundary

Primary sources:

- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5
- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
- https://docs.llmgw.ru/models

Apply explicit outcome/scope, complete work before checking in, real executable
verification and deliberate frontend composition. A native Anthropic effort or
thinking setting is not automatically supported by this OpenAI-compatible
provider route. The current adapter does not forward its internal thinking
budget; no unverified reasoning parameter or larger retry limit is introduced.
Truncated or malformed tool responses remain failures, not completed builds.

## Delivery and acceptance

Local API/Gateway tests, lint, types and independent defect review precede push.
Merge through the configured GitHub branch, require exact-main CI, then use
`infra/release/deploy-prod.sh SHA --no-web --gateway --repair-window CURRENT`.
Preserve actual repair-window, legal/SMTP/OAuth credentials, web identity and
compiled MAX Core pins. No orchestrator/Core certificate input changes are part
of this release. Back up production configuration/database and drain existing
operations before service replacement; verify both controllers, all workers,
Gateway writer identity/settlement schema, HTTP health and production smoke.
If a gate fails, retain the drain and fix forward; never forge release evidence.

After delivery, run two ordinary generations concurrently and record actual
model, elapsed time, terminal state and any failure phase. Inspect rendered
screens at mobile/desktop dimensions and perform the real primary workflow.
Add representative business data, edit/regenerate, verify persistence and run
the app inside MAX. A green compiler or HTTP response does not count as visual
or functional acceptance. Aim for eight consecutive accepted generations;
record failed attempts rather than removing them from the denominator.

Pending: production delivery, latest-model application generations, visual
acceptance and MAX acceptance. Existing CRM demo/bot/data remain preserved.
