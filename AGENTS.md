# Product doctrine (highest priority, non-negotiable)

Omnia exists to create complete production digital products for businesses, not
mockups, disposable prototypes, decorative demos, or code that merely compiles.
Every agent and every Codex session working in this repository must optimize for
the real business outcome: establish the business's digital footprint and ship
a product that solves the requested operational or commercial problem end to end.

The default meaning of "build", "generate", "finish", or "ready" is a
production-capable result. It includes, where relevant, a real domain model,
authenticated and persistent data, complete user flows, integrations, truthful
loading/empty/error/success states, security and tenant isolation, legal and
privacy requirements, accessibility, observability, tests, deployment, and
executable release proof. A screen, frontend shell, hardcoded dataset, fake
success message, or HTTP 200 is never sufficient evidence of completion.

Use agentic engineering to pursue the result: understand the business problem,
create and maintain an observable plan, inspect the existing system, implement
the full vertical slice, run it, exercise real user actions, verify persistence
after reload, inspect the rendered product, repair defects, and complete the
delivery loop. Prefer the maximum relevant business value, depth, and quality;
"maximum" never means unrelated feature bloat or weakening reliability.

Mocks and demo data are allowed only when the owner explicitly requests a
prototype or when they are clearly labelled temporary scaffolding inside an
unfinished run. They cannot satisfy completion, acceptance, release, or
production-readiness gates. If credentials, legal data, external approval, or
another owner-controlled dependency is missing, finish every unblocked part,
name the exact blocker, and describe the result as incomplete rather than silently
downgrading it to a demo.

This doctrine applies to every generator and surface, including ordinary web
projects and MAX Mini Apps. It overrides MVP shortcuts, preview-first behaviour,
and narrower historical instructions unless the owner explicitly asks for a
prototype in the current task. The durable elaboration is in
[`docs/PRODUCT_DOCTRINE.md`](docs/PRODUCT_DOCTRINE.md).

# Delivery rule (mandatory)

Every change in this repository must complete the full delivery loop:

1. Verify the change with the repository-appropriate tests, lint/type checks, and diff/data sanity checks.
2. Commit all intended changes with a clear message.
3. Push the current branch to its configured upstream (normally `origin/main`).
4. Deploy the pushed revision to the configured production server using the documented production compose/project.
5. Confirm deployment health (service status and relevant HTTP/health endpoints) and report the revision, push, deploy, and health evidence.

No silent exceptions are allowed. If verification, commit, push, credentials, SSH, deployment, or health confirmation fails, report the exact failure and do not claim completion. Do not leave a change described as complete while it remains undeployed; future work must resume the delivery loop before starting unrelated changes.

Use only the documented production deployment path. Do not deploy the development `infra/` stack in place of production. Preserve unrelated working-tree changes and never force-push or rewrite history.
