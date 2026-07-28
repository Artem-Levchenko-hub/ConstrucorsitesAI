# Delivery rule (mandatory)

Every change in this repository must complete the full delivery loop:

1. Verify the change with the repository-appropriate tests, lint/type checks, and diff/data sanity checks.
2. Commit all intended changes with a clear message.
3. Push the current branch to its configured upstream (normally `origin/main`).
4. Deploy the pushed revision to the configured production server using the documented production compose/project.
5. Confirm deployment health (service status and relevant HTTP/health endpoints) and report the revision, push, deploy, and health evidence.

No silent exceptions are allowed. If verification, commit, push, credentials, SSH, deployment, or health confirmation fails, report the exact failure and do not claim completion. Do not leave a change described as complete while it remains undeployed; future work must resume the delivery loop before starting unrelated changes.

Use only the documented production deployment path. Do not deploy the development `infra/` stack in place of production. Preserve unrelated working-tree changes and never force-push or rewrite history.
