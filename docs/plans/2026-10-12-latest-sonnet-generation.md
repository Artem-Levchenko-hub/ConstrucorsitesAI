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

### Instructions selected for Yleum

The general guidance and the model-specific Sonnet guide are complementary.
This is a product-specific adaptation, not a claim that Anthropic has certified
our prompt or that a provider alias identifies a fixed model version.

- State the requested working outcome and scope; finish that outcome without
  adding unrelated features. Separate request, existing files and tool feedback.
- Choose a visual direction for the actual audience. Make typography, spacing,
  color pairs and the primary action consistent across the application's screens.
- Preserve existing data and working features. Repair the reported failure using
  current source and previous check feedback instead of repeating an earlier fix.
- Run checks that exercise the change before claiming completion. If the agent
  cannot inspect rendered screens, record that limitation for the final verifier.
- Use a concrete acceptance path: user action, expected screen, saved record,
  and result after reopening. Include representative populated, empty and error
  states rather than one shared decorative template.
- Calibrate quality/latency controls with our own evaluations only after the
  transport supports them. Native API recommendations for effort, thinking and
  output limits are not instructions to blindly pass those parameters to Gateway.

For paired live evaluations, additionally check MAX light/dark themes, mobile
keyboard and safe areas, long input, focus, repeated submission and reopening.
These are acceptance tasks, not evidence supplied by the prompt itself.

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

## First delivered evaluation

Revision `75ddf81525b3b8057f1bfe964e7d0ec84dcb1004` completed exact-main CI,
production delivery and production smoke. All six generation roles resolve to
the requested latest alias; the existing disabled native-generation flag,
compiled Core, web revision and CRM demo/bot/data were preserved.

Two real generations on separate authorized profiles ran concurrently. Both
recorded 29 completed calls to the latest alias and failed final acceptance:

- Coffee: 505.9 seconds of execution. The capability matcher interpreted
  "Не выдумывай историю заказов" as a positive request for history. Source checks
  passed, but this invented requirement rejected the product. Source repair
  made no source changes and correctly stopped rather than accepting it.
- Workshop: 447.6 seconds of execution. The full Next.js build rejected an
  exported `STATUS_LABELS` constant in `src/app/page.tsx`; the existing finalizer
  classified that source/type error as terminal instead of offering bounded
  source repair.

These observations are 0 accepted products out of 2 attempts, not evidence of
stable generation. Neither app has passed rendered UI, business-data or MAX
acceptance. The underlying model version behind the provider alias is still
unconfirmed. Provider RUB receipt amounts were absent; platform cost estimates
must not be reported as verified provider charges.

The next iteration addresses the two reproduced failures separately. Negative
mentions must not invent requested capabilities, while explicit positive
requirements remain enforced. Confirmed compiler/type failures may enter the
existing bounded source-repair path; infrastructure failures, timeouts and
unknown outcomes must remain terminal. Separately, missing/unknown/filtered
provider stop reasons must never become successful completion or executable
actions. Actual provider receipts and settlement remain required even when
the output cannot be used; terminal output errors must not trigger blind retries.

That repair was delivered as `e65848f11064a9c3586914821c38b570682c5cee`.
Exact-main CI and production smoke passed; API, workers and both controllers
match that revision. Web and compiled MAX Core stayed unchanged, and protected
CRM/MAX data and settings passed the release preservation checks.

## Second paired evaluation: build success, product failure

Both repeated generations completed with accepted build/runtime evidence.
Workshop took 647.5 seconds total (12 model calls); coffee took 650.4 seconds
(20 calls). Receipts confirmed the latest alias. These are two completed builds
out of four attempts, not two accepted products or a stable success rate.

Independent source inspection and real coffee UI interaction found:

- Cart UI writes `quantity`, while its generated API normalizes `qty`. The
  API silently persists an empty list; optimistic UI shows one item until reload.
- The request form omits its own route's required idempotency key. Its history
  reader also expects `total` and expanded items, while the API returns
  `totalRub` and product identifiers/quantities. These are product-owned DTOs,
  not fields to impose on all managed MAX APIs.
- The product's fixed bottom navigation has no stacking layer. A live DOM
  hit-test found the required platform footer's privacy link above the cart
  button, explaining its unexpected navigation.

The next vertical slice corrects the common generation instructions: inspect
actual routes/normalizers, share or explicitly map validated client/server DTOs,
preserve stable operation identity across uncertain retries, and verify actual
client payloads and response rendering. SQL storage tests alone cannot prove
that the UI sends the same contract. Preserve the platform footer and make both
its links and product navigation accessible with safe-area/layout checks.

The workshop's normal Yandex-owner browser flow created one disposable QA order,
changed its status from diagnostics to repair, and retained its title, description
and new status after reload. This is positive UI/persistence evidence, but not
cross-user isolation, regeneration or real MAX acceptance. Retain that QA record
for the next regeneration test; do not clear the database to make checks pass.

Acceptance remains real UI add -> reload -> unchanged cart -> submit -> retry
the same operation -> one history entry -> reload, followed by regeneration
with existing data and the independent MAX flow. Do not weaken security or
replace these checks with compiler success. The accepted-product streak is zero.

## Third pair: execution guard failure, not a provider/compiler failure

On deployed `f6b6745f`, workshop completed with 30 completed provider receipts
and accepted build evidence. Coffee failed with 37 completed provider receipts:
`generation stopped (exploring): stuck exploring (reading) without writing any file; rolled_back`.
The six-attempt cohort has three completed builds and three failures. No full
product streak is established; the new workshop priority UI still needs acceptance.

The Coffee loop requested `build` on steps 27 and 34. Both requests were skipped
by the no-write guard before executor invocation. Their preliminary `ok: true`
events were not compiler results. Existing accepted snapshot/publication identity
survived the failure; this does not independently prove all business rows unchanged.

Reuse PR82's executed-source-progress and truthful skipped-step implementation
on top of current main. Preserve actual source-change witnesses, repeat limits,
the total turn budget and coordinator proof. Additional regressions cover one
necessary build after new source at the hard no-progress threshold, skipped
read signatures, and consistent coordinated finalization instructions. No
unbounded verification exception or arbitrary shell exemption is authorized.

Fix routing separately: negative recreation commands and a server response are
not requests to rebuild architecture. Retain first-build, selected-element and
positive backend/rebuild behavior. The exact Coffee repair is a focused edit.

## Official Anthropic instructions and delivery

The current [Sonnet 5.5 guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5),
[Sonnet 5 design guidance](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5)
and [Anthropic frontend design skill](https://github.com/anthropics/claude-code/blob/main/plugins/frontend-design/skills/frontend-design/SKILL.md)
support a product-specific brief before new UI/redesign: main action, named
palette values, typography roles and layout hierarchy. Focused edits preserve
the existing design and records. Use independent rendered/user-flow acceptance;
the generator's summary and a green compiler are insufficient.

Effort and native tool settings are not changed in this patch. Our classic
OpenAI-compatible Gateway route is distinct from direct Anthropic Messages;
source repair can still use native tools with the main native flag disabled.
The [5.5 migration rules](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide)
need a separate transport capability check. Latest alias receipts prove the
requested routing label, not the underlying physical 5.5 version.

This combined patch changes API execution and web progress rendering. Deliver
both from exact pushed main, retaining schema, compiled Core, model overrides,
legal/SMTP/OAuth settings, existing CRM/MAX bindings and data. Prepare a fresh
release record from API/controllers `f6b6745f` and web `684e4a2e`, with independent
review, exact CI, backup/drain, exact service/HTTP identity and production smoke.
No fourth paid pair is launched until that delivery is verified. Keep historical
failures in the denominator; resume the same two QA projects, not replacement
projects or a reduced product acceptance definition.
