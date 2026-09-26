# Delivery rule (mandatory)

Every change in this repository must complete the full delivery loop:

1. Verify the change with the repository-appropriate tests, lint/type checks, and diff/data sanity checks.
2. Commit all intended changes with a clear message.
3. Push the current branch to its configured upstream (normally `origin/main`).
4. Deploy the pushed revision to the configured production server using the documented production compose/project.
5. Confirm deployment health (service status and relevant HTTP/health endpoints) and report the revision, push, deploy, and health evidence.

No silent exceptions are allowed. If verification, commit, push, credentials, SSH, deployment, or health confirmation fails, report the exact failure and do not claim completion. Do not leave a change described as complete while it remains undeployed; future work must resume the delivery loop before starting unrelated changes.

Use only the documented production deployment path. Do not deploy the development `infra/` stack in place of production. Preserve unrelated working-tree changes and never force-push or rewrite history.

## MAX versioning and restoration work

- For live rollback/readiness work, identify the local, pushed, and live revisions where applicable and reproduce the smallest safe failing user flow before a detailed plan or implementation. Use an isolated test app and existing version pairs where possible; protect customer data and avoid extra AI generations or paid canaries without a concrete evidence gap. If live testing is unavailable, record the exact blocker and continue safe independent checks.
- For full restoration acceptance, close these cases one at a time: (1) a version with no business data restores and runs; (2) compatible business data survives an exact, AI-free restore; (3) incompatible data produces `needs_changes` and is adapted only after the explicit “Адаптировать и восстановить” action and normal generation admission. For each case record expected/actual behavior, revision and environment, relevant UI/API/log evidence, data persistence, and the next falsifying check.
- Keep one short checkpoint with accepted results, current blocker, changed files, and next action. Search the affected modules first; show bounded excerpts while preserving complete relevant logs. Reuse evidence and passed checks until a code or environment change could invalidate them.
- Delegate only independent, bounded work that improves completion time or assurance. Give each agent a specific question, file ownership when editing, and a stop condition; keep Git and deployment serial. Avoid repeated agent polling and status messages without new evidence. Retain independent review, live acceptance tests, and the full delivery rule above.
