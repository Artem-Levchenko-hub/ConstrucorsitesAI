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

MAX Mini Apps use the same native code-agent loop as other production container
applications, with MAX-specific locked runtime, signed identity, Bridge, managed
integrations, legal routes and secret boundaries. Before product code, the
server-owned Design Director persists a deterministic DesignDNA: audience,
emotional promise, three materially different concepts, one selected direction
with rationale, composition, typography, semantic colours, geometry and density,
data-visualisation language, motion, signature interaction and anti-patterns.

The selected direction is not a reusable screen template. A deterministic premium
mobile foundation supplies accessibility and interaction contracts for navigation,
sheets, forms, charts, async/offline states, touch targets and safe areas; a domain
skill supplies product depth. Relevant managed capabilities (AI, payments, leads,
catalogue, analytics, persisted actions, MAX identity and legal) are planned with
truth requirements and executable evidence. Fake success and unavailable visual
proof are release failures.

Every completed MAX revision must carry an independent signed visual verdict at
360px and 390px, plus functional and capability evidence. A generic or rushed
render gets at most two targeted repair passes in the bounded native loop; if the
quality floor remains unmet, the last known-good product is preserved and no new
snapshot is published. See [`MAX_APP_ENGINEER.md`](MAX_APP_ENGINEER.md) for the
architecture and proof chain.

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
