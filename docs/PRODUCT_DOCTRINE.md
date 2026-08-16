# Omnia product doctrine

## Mission

Omnia creates complete digital products that solve real business problems. Its
output is not limited to a website or an attractive interface. Depending on the
business need, the product may include a public digital presence, a customer
application, an operator workspace, data and workflows, lead capture, sales or
booking, automation, integrations, analytics, support and production delivery.

The goal is an end-to-end business result: a product that can be operated,
measured, supported and extended after launch.

## Default contract

Unless the owner explicitly asks for a prototype, every generation and every
engineering task uses **production-capable application** as its definition of
done. Relevant requirements include:

1. The business problem, audience, primary job and measurable outcome are clear.
2. The product has a complete domain model and real user flows rather than a
   collection of disconnected screens.
3. User-owned data is authenticated, isolated and persisted; a reload does not
   erase completed work.
4. Primary actions have truthful loading, success, empty, error, retry and
   recovery behaviour. Failed operations never display fake success.
5. Required integrations use real managed interfaces. Secrets are never embedded
   in generated client code, logs, prompts or snapshots.
6. Security, privacy, legal, accessibility and mobile behaviour are implemented
   to the level required by the product and its data.
7. The result is tested through executable user scenarios, not inferred from
   compilation, source inspection, HTTP 200 or hydration alone.
8. The rendered experience receives visual and interaction review. Generic,
   rushed or unreadable output is unfinished even when technically functional.
9. The release is versioned, deployable, observable and recoverable. Required
   evidence is stored with the delivered revision.

## Agentic engineering loop

Every coding agent follows the same outcome-driven loop:

1. Translate the request into a business brief and an observable implementation
   plan covering screens, domain entities, capabilities, integrations and risks.
2. Inspect the current product and platform contracts before editing.
3. Implement complete vertical slices, including backend, persistence and states,
   rather than polishing a frontend mock first and declaring success.
4. Build and run the product in its real runtime.
5. Exercise navigation, primary actions, persistence after reload, isolation,
   failure paths, responsive behaviour and accessibility.
6. Inspect the rendered result and repair functional and visual defects.
7. Publish only when the relevant release proofs are green. Otherwise preserve
   the last known-good version and report the exact unfinished requirement.
8. Complete verification, commit, push, deployment and health confirmation under
   the repository delivery rule.

## Prototype exception

A prototype is an explicit exception, never an implicit fallback. Demo or mock
data must be visibly labelled, isolated from production claims and recorded as an
unfinished dependency. Prototype evidence cannot satisfy a production acceptance
or release gate.

## MAX App Engineer

Fresh MAX builds use a stricter kernel-owned lifecycle. The completed questionnaire
becomes one durable ProductSpec containing purpose, audience, screens, capabilities, primary
action, data/history, integrations, style and acceptance criteria. Omnia derives a
non-empty file/screen/scenario plan and selects the smallest relevant capability
packs before the model runs. The model receives this one canonical task, writes one
coherent multi-file product revision and may make one evidence-driven repair.

Build, live runtime and signed functional checks are automatic state transitions,
not model tools. Completion proves that every planned screen is reachable, the
primary action works, required user data survives reload, failures remain honest,
and mobile/accessibility/browser invariants are green. Subjective screenshot scoring
and repeated redesign are not release gates for this lifecycle; the selected style
is applied once. A red final contract preserves the last known-good product and no
partial snapshot is published. See [`MAX_APP_ENGINEER.md`](MAX_APP_ENGINEER.md) for
the exact state machine and proof chain.

If an external dependency is unavailable, agents still complete all safe,
unblocked engineering work. They then identify the exact owner or provider action
required and keep the product's readiness status honest.

## Maximum value without feature theatre

"Build it to the maximum" means maximize relevant business value, completeness,
reliability and quality. It does not mean adding unrelated features, unnecessary
infrastructure or decorative complexity. Every major capability must connect to
the business outcome, a real user need or a production obligation.

## Enforcement

This file explains the mandatory rule in the repository-root `AGENTS.md`. The root
rule is automatically loaded by Codex sessions opened from this repository, so a
fresh checkout on another computer inherits the same doctrine.

Generator prompts, completion contracts, functional gates and release
attestations should encode this doctrine as executable checks. Documentation
alone is not a substitute for those checks, and later changes must not weaken
them in the name of a faster preview or a simpler MVP.
