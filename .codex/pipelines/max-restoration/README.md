# MAX restoration agent pipeline

This pipeline freezes the test contract before production implementation, then runs a
bounded Sol implementation loop with deterministic gates and independent Astra review.

## Prerequisites

- Windows PowerShell 7 (`pwsh`), Git, Docker Desktop/Engine, Python 3.12, `uv`, Node 20+
  and pnpm 9.15.
- Codex CLI authenticated on the machine with access to `gpt-6-astra` and
  `gpt-5.6-sol`.
- A clean checkout. The pipeline creates a `codex/max-restoration-*` branch and never
  force-pushes, deploys, or touches production credentials.
- Disposable PostgreSQL/Redis/Docker resources required by the generated gate runner.

## Validate the package

```powershell
pwsh -NoLogo -NoProfile -File scripts/run-max-restoration-agent-pipeline.ps1 -ValidateOnly
```

## Run

```powershell
pwsh -NoLogo -NoProfile -File scripts/run-max-restoration-agent-pipeline.ps1
```

Artifacts are written to a timestamped directory under
`.artifacts/max-restoration-pipeline/`. A successful
run ends with `pipeline-result.json` and leaves a reviewed, uncommitted candidate on the
new branch. The local full gate is bound to a controller-computed tree digest before and
after testing. It is not release evidence. Commit the candidate, rerun the release-mode
gate on that exact SHA, then push, deploy and run live acceptance through the repository
delivery runbook after a human release decision.

The loop stops after four implementation iterations by default. It also stops earlier
when the same normalized failure fingerprint occurs twice. Raising the limit requires
an explicit `-MaxIterations` value and cannot exceed eight.

The automation follows the official Codex non-interactive pattern: prompts are passed
through stdin to `codex exec`, progress is captured as JSONL, final reviewer output is
validated by a JSON Schema, author/implementation stages use `workspace-write`, and
review stages are read-only.
