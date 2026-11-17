# Short MAX brief preparation

New initial MAX builds can make one optional, non-streaming brief-preparation
request before coding. `PROMPT_PREPARATION_ENABLED=false` disables it. Eligibility
is frozen in new admissions; a rollout never opts old unfinished runs or edits
into a new paid step. Manual retry admissions may reuse preparation, not create
a missing one. Existing prepared execution entries remain byte-for-byte inputs
to continuation.

## Inputs and output

The service receives one owner's description (at most 6,000 characters, with
head/tail retained for longer descriptions) and bounded explicit color, tone,
theme and font preferences. Fenced code in that description is omitted. No
workspace files, source snapshots, project memory, transcript, selected HTML,
image contents/URLs or font assets are loaded into this request. Resolving a
manual retry identifies its single original owner request; it does not send the
intervening conversation.

The instruction asks for a short task-specific advisory note: goal, explicitly
requested behavior, audience and visual direction. It borrows owner-first
hierarchy, typography, spacing, useful states and plain copy principles from
frontend-design and Impeccable. It supplies no industry-to-screen mappings,
template catalog, fixed navigation count, palette recipe, screenshot audit or
repair pass. Unknown facts remain unknown.

The unchanged full owner request and platform rules still go to the coding
agent. The note is additional user-message data, never a system instruction,
acceptance proof, executable plan or source-file seed. Requested API-only work,
edits, continuations and restoration adaptations create no preparation request.

## Expense and limits

The `prompt_prepare` model role defaults to the existing primary model and can
be configured through `ROLE_MODELS`. Output is capped at 900 tokens, with a
30-second overall wall-clock limit. There is no response-refinement call.

This optional service is included in generation pricing: it does not debit
another generation unit or separately debit the customer's wallet. The client
wrapper explicitly uses generic service billing (`free=true`), preserving the
real owner/project attribution and the `prompt_preparation` expense stage, but
omitting run and message identity. Ordinary coding calls retain their existing
billing and recovery context. Provider costs still belong in the independent
expense journal; `expense_owner=user` denotes attribution, not an extra debit.

The at-most-once guarantee is one **API-to-gateway request** for an owned input
fingerprint. The gateway's existing proven preconnect/refusal handling may
reach a reserve provider; ambiguous accepted requests are not replayed. This
does not promise a single TCP attempt or exactly-once delivery by the provider.

## Durable cache and failures

`generation_runs.agent_state.prompt_preparation_v1` stores a version, input
SHA256, claim UUID, state and bounded result. It stores no second copy of the
description or source code. The key includes the complete description and
preferences, not just the shortened transmitted excerpt. Cache scope is the
same project and owner. A different request/preference can create one new
preparation; a repeat of the same input reuses its saved result.

A short project-scoped advisory lock serializes cache lookup/claim. The claim
commits **before** HTTP. No database lock spans the model request. Successful
output is saved with a claim CAS and JSONB namespace update, preserving other
generation/recovery state. Ownership, messages and running status are checked
again when saving. A prepared execution entry is never retroactively replaced
by a late response.

An existing claimed, unknown, failed or malformed record never causes another
POST. Empty, overlong, timed-out or unavailable preparation falls back to the
original request. Cancellation/crash can leave a claimed record: coding can
continue without the note, and the optional call is not paid again. A response
lost before cache commit may remain unavailable; this deliberately favors
at-most-once expense over guaranteed recovery of an optional note. Raw provider
errors and owner descriptions are not written to application logs.
