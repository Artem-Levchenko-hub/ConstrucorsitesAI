# QA generation engines

The MAX editor uses the default Sol coding engine for both ordinary and QA
accounts. Completing the wizard submits its saved brief on the empty project
with a stable project-specific key. A refresh after an unconfirmed response
uses the same key. The server applies its normal phone, ownership, budget and
admission checks. The editor offers no model selector. Existing explicit API
choices remain supported, and the legacy `model_id` input remains ignored.

Admission saves the Sol selection in `GenerationRun.agent_state` and freezes
`gpt-6.2-sol` in the existing dispatch's `model_id` and `force_model`. An omitted
engine on the same accepted submit key adopts its saved choice under the
project lock; an explicit opposite engine produces an identity conflict.
The browser queue preserves explicit choices and legacy omissions with the
same key across reload. Fresh manual choices are captured before credential
intake or queueing. A stable wizard handoff without an engine keeps
that omission: the server resolves a new request to Sol or replays its previous
choice. A worker
continuation reads the accepted dispatch, independently of the current UI
selection. Historical runs without an engine selection keep their prior routing.

Sol uses LLMGW's `openai/gpt-6.2-sol` Chat Completions text surface and the same
tagged JSON action protocol as the production text agent. Initial coding,
edits, escalation and finalization repair retain this model and protocol.
The guarded file executor, snapshots, data protection, build checks, preview
and response accounting remain shared. Sol cannot enter the Anthropic native
Messages route or silently fall back to Claude/AITunnel.

At the upstream boundary Sol omits `temperature`, uses
`reasoning_effort: "medium"`, and translates an explicitly supplied legacy
output budget to `max_completion_tokens`. Ordinary coding and Sol repairs
omit an automatic output cap and use the provider's default. This is not an
unlimited provider response. Length/filter/refusal outcomes retain the existing
receipt and recovery rules; partial actions are not accepted as completed work.

Sol forwards the remaining caller budget to the Gateway after reservation
waits; SQL and retries spend the original clock. The Gateway's existing cap
and safety reserve still apply. Neither transport nor response capture grants
unlimited time or restarts the generation deadline.

Non-streaming Sol coding calls belonging to an owned, running generation
already admitted as Sol use retained provider responses independently of
full V3 executable recovery enrollment. The frozen dispatch remains the engine
authority. Capture does not change accepted-build billing or admit another
generation, and ordinary unit reservations remain unchanged. Both `generation_provider_attempts_enabled` and
`generation_response_retention_enabled` are required; disabling either refuses
these calls before paid dispatch rather than falling back to an uncaptured POST.

If a local body is lost while the same call still has time, the warm process
can read or reconcile that exact owned attempt's retained response without
another model POST. Missing or unconfirmed evidence remains an error. Capture
alone does not enable a full cold-run resume: executable recovery still needs
its separate admission and acceptance. Previously failed runs are not migrated
or retroactively given retained responses by this change.

The optional cached prompt preparation remains the existing shared service
with its own role configuration. Selecting Sol changes the coding engine,
not that service or the project's runtime AI model. No design templates,
parallel product agents or extra visual review calls are introduced.

A read-only question about committed source keeps its existing shared role
and text-only behavior. Its persisted readonly marker, clarify mode and absence
of a coding dispatch exclude it from Sol coding capture. A build or edit with
a missing or invalid dispatch still fails closed; this exception cannot enroll
full recovery or authorize another coding POST.

Deploy API, workers, Web and Gateway together after the exact revision's CI:

```sh
bash infra/release/deploy-prod.sh <full-main-sha> --gateway
```

Use the documented release guards, drain and native transport preflight. A
Web-only release cannot enable this feature on an older Gateway. Verify the
wizard handoff with mocked submission; a full model comparison is a separate
paid control, not an automatic deployment check.
