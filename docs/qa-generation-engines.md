# QA generation engines

The MAX editor exposes Claude Sonnet 5.5 / GPT-6.2 Sol only when
`GET /api/auth/me` returns `can_choose_generation_engine: true`. Authority is
the existing active, private `qa_internal` plan together with the persisted
admin role. An email, browser flag or ordinary paid plan does not grant access.

Claude remains the default. The selector controls the **next request**; changing
it does not start a generation. Each explicit submit sends the closed
`generation_engine: "claude" | "sol"` option. The legacy `model_id` input remains
ignored. A Sol request for an ordinary account or an unowned/non-MAX project
is refused before reservation or model work.

For a QA project's first wizard handoff, the editor stages the description in
the composer instead of starting a paid call before the engine can be chosen.
The server-rendered session supplies this capability before hydration. An
explicit Send uses the original stable handoff key; refresh or an unknown
acceptance retains that handoff until success. Ordinary starter auto-submit
keeps its existing behavior.

Admission saves the Sol selection in `GenerationRun.agent_state` and freezes
`gpt-6.2-sol` in the existing dispatch's `model_id` and `force_model`. Replaying
the same submit key with another engine produces an identity conflict.
The browser queue also preserves the choice and key across reload. A worker
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
already admitted as QA Sol use retained provider responses independently of
full V3 executable recovery enrollment. The frozen dispatch remains the engine
authority. Capture does not change accepted-build billing or admit another
generation. Both `generation_provider_attempts_enabled` and
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

Deploy API, workers, Web and Gateway together after the exact revision's CI:

```sh
bash infra/release/deploy-prod.sh <full-main-sha> --gateway
```

Use the documented release guards, drain and native transport preflight. A
Web-only release cannot enable this feature on an older Gateway. Verify the
QA selector without submitting a prompt; a full model comparison is a separate
paid control, not an automatic deployment check.
