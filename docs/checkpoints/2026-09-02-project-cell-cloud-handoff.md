# Project Cell local continuation — 2026-09-02

## Objective and completion boundary

Deliver the full owner-only Docker Project Cell and the first real MAX frontend/backend/PostgreSQL application. Preserve legacy routing; prove isolation, recovery and production health. The user will personally inspect the first generated application. Do not redefine completion as a working resource bundle or a successful typecheck.

The accepted full contract is `docs/superpowers/specs/2026-09-01-docker-project-cell-owner-canary-design.md`, especially sections 3, 15, 16, 17 and 20. K3s/Kata and public multi-tenant cell routing remain out of scope and disabled.

## Current checkpoint

- Latest user direction: continue locally, as fast as practical. The cloud task
  finished; its published candidate-persistence commit `18c6b1df` was integrated
  into `codex/project-cell-cloud-20260902`. Do not restart cloud execution or
  transfer workstation credentials.
- Base revision: `d81295f684356629bd02d0f1c91e3f9a2a428477` on `origin/main`.
- This continuation branch preserves subsequent fixes. It is an unfinished development checkpoint, NOT a completed release.
- Latest source revision has not been deployed. Owner routing remains OFF in production; do not enable it while the verified architectural gaps below remain.
- Preserve unrelated local screenshots, documents and `.artifacts/`; they are intentionally not part of this branch.
- Primary agent is the sole serial owner of commit/push/migrations/deploy. Other agents use disjoint ownership and read-only review.
- Use Sequential Thinking for consequential design and Context7 for external dependencies when available; otherwise use primary official documentation and disclose unavailable tools. Never claim a tool was used when unavailable.

## Fixes preserved here

1. MAX shell operator kill switch remains authoritative; cell readiness cannot bypass `MAX_PROJECT_SHELL_ENABLED`.
2. Bootstrap `OrchestratorBadRequest` maps to controlled executor-unavailable behavior.
3. Explicit empty-file writes, deletes and rollback are distinct across the cell/preview APIs. Legacy empty-string-delete behavior remains compatible.
4. Composite pause/destroy records the outer durable operation before checkpoint side effects; replay does not reseal an already-created checkpoint.
5. Failed restore plus successful rollback is recorded as failed, never successful replay. Corrupt/missing artifacts are rejected before starting a restore journal.
6. Cell PostgreSQL initialization adds an idempotent same-network SCRAM HBA rule; no host ports are published.
7. Agent commands use the bundle's remaining reserved memory/CPU (default 1 GiB and 0.5 CPU), not the 256 MiB filesystem-helper cap. Swap is bounded by the same memory limit. Enabled cell configurations below 1 CPU fail at configuration validation; disabled legacy configuration remains compatible.
8. Corepack uses the image's `/home/node/.cache/node/corepack` cache with Corepack network access disabled.
9. Build temporarily reuses bundled dependencies only when dependency metadata and lockfiles match. This guard is NOT the final package-installation contract: implement controlled egress and persistent dependency installation rather than silently reducing product scope to baked dependencies.

## Verification

- Full orchestrator suite including the CPU-validation change: **639 passed, 5 skipped, 15 xfailed** in 16.56 seconds.
- Orchestrator Mypy: **51 source files clean**.
- API messages/project-cell-executor/orchestrator-client focused suite: **44 tests**, completed with exit 0 against a disposable PostgreSQL database.
- API Mypy on the three changed source modules: clean. Scoped Ruff and diff checks passed.
- Independent review identified and fixed the corrupted-checkpoint journal issue and low-CPU configuration issue. Re-run final gates and review any subsequent changes.
- Earlier full API suite has a baseline taste/reference visual gate failure in `tests/test_acceptance.py::test_evaluate_passes_clean`; do not report the entire API suite green.
- Earlier Docker lifecycle test passed, but lifecycle tests do NOT prove a real application build or the full owner contract.

Local PostgreSQL testing note: an unanchored WSL distro shut down after shell exit and stopped the disposable database. Attaching a live `docker start -a` session kept WSL running and allowed all 44 focused API tests to pass. This was an environment issue, not an application fix. Cloud tests should use their own disposable database and never platform production data.

## Verified architectural gaps — must finish, not waive

### Resident agent

- `apps/agent-runner/src/omnia_agent_runner/runner.py` contains `TrustedRunner`.
- `service.py` exposes `/runs`, `/healthz`, `/readyz`; `messages_auth.py` provides runner JWT authentication.
- Gateway `apps/llm-gateway/src/omnia_gateway/routers/messages_native.py` supports `/v1/project-cell/messages`.
- No live API/orchestrator dispatch into that resident service was found. `messages.py` still runs the loop API-side with `project_cell_executor.py` as an action adapter.

### Persistent processes, draft runtime and browser

- `docker_py_cell_backend.py::run_workspace_command` creates and removes an ephemeral helper per command. It is not a persistent supervisor.
- The cell resource/state model currently includes volumes, networks, PostgreSQL and Redis, not resident runner/draft/browser/supervisor containers.
- Draft preview, hot reload and visual QA still resolve legacy `omnia-dev-*` runtime containers and API-side browser workers.

### Database split

- Cell shell/build uses the dedicated cell PostgreSQL from `routers/workspace.py::_workspace_agent_exec_env`.
- Preview provisioning in `services/provisioner.py` uses `postgres_admin`'s shared-schema DSN and `runtime_db_container_name`.
- Therefore preview currently does NOT validate the application against the cell's database. Bind the cell-owned draft runtime and preview to the cell DB before claiming end-to-end success.

### Remaining safety/product seams

- Networks are currently internal-only; controlled public egress and real dependency installation are unfinished.
- Complete durable process/session ownership, descendant cancellation and restart recovery.
- Verify/implement candidate evidence and atomic accepted-release promotion, including database backup/migration references. Workspace/operation tables alone do not establish that contract.
- Do not start a legacy writer in parallel after a cell gets a writable lease.

Recommended integration order: cell-owned draft runtime + DB/preview binding; resident runner dispatch; persistent process/browser lifecycle; controlled egress/install caches; candidate/promotion/cancellation/recovery proof. Use existing interfaces rather than replacing the whole platform.

## Real runtime canary findings

On a disposable server cell, before these fixes:

- PostgreSQL rejected cross-container connections (SQLSTATE 28000 / missing HBA entry).
- The persistent root home hid Corepack cache; pnpm attempted a registry download and failed on isolated networking.
- Unconditional pnpm install tried to recreate image-owned `/app/node_modules` and failed EACCES.
- Skipping installation reached TypeScript, which was killed with exit 137 at 256 MiB.
- Diagnostic-only patches proved DB, Redis and workspace access, stale-write/exec rejection with 409, and preservation of command exit code 23.

Final verification must run the actual final source without diagnostic monkeypatches. A canary proof table must be dropped after its isolated CRUD smoke, before Drizzle schema push, to avoid an artificial interactive rename prompt.

## Production state and delivery

Revalidate everything before mutation; these are last observed facts, not perpetual guarantees:

- Checkout `/opt/omnia` is at d81295f6, but running services are not all switched to that revision.
- Canonical compose: `/opt/omnia/apps/llm-gateway/deploy/full/docker-compose.yml`, project `full`; orchestrator systemd unit `omnia-orchestrator.service`.
- Do NOT substitute the development `infra/` stack.
- API/web d81295f6-tagged images were built; final revision needs its own exact tags.
- Existing release backup/candidate files: `/opt/omnia-runtime/releases/project-cell-d81295f6`. Never print their secret contents.
- Backup checksums were verified under `/opt/omnia-runtime/backups/20260901-001501`.
- Last Alembic head: `0053_project_cell_operation_fencing`.
- Server has unrelated dirty files; check overlap using exact incoming paths and preserve unrelated work.
- Some pre-existing generated runtime containers were restarting at the last observation. Record baseline health; do not silently claim all previews healthy or mutate unrelated applications.

Full loop: verify/review → focused commit → push → check no active generations and backups → deploy exact pushed revision with flags OFF → exact API/web/worker/orchestrator health → clean real runtime/isolation/recovery canaries → owner-only allowlist enablement → real retained MAX project and acceptance proof.

## Cloud and credentials

An authenticated Codex Cloud environment has been created for this repository with locked dependency setup. This document does not establish that a task is running; verify the cloud task's actual status.

No workstation key or production secret has been copied into this branch or cloud prompt. Codex Cloud Secrets are setup-only; do not write them to agent-readable files to bypass that boundary. A production release channel must be explicitly configured with minimal authority (for example, a protected GitHub Actions environment), not a bulk copy of workstation credentials. If that channel is absent, continue all safe implementation/testing work and report the deployment blocker accurately; do not invent a successful deployment.

## Cloud continuation update — candidate promotion slice

The next vertical slice adds the durable, immutable Project Cell release-candidate
**persistence primitive** and a fenced compare-and-swap candidate transition. A candidate binds source revision,
migration digest, database-backup reference, build reference and verification
reference to the active generation run and fencing epoch. Cancelled candidates,
stale epochs and candidates prepared against an older accepted release cannot
promote. Promotion also locks and verifies that the authoritative generation run is
still running and belongs to the workspace owner. The partial unique index permits
only one accepted candidate per workspace. This primitive is not yet wired to the
Project/Snapshot accepted release pointer, so the overall atomic product-promotion
contract remains open.

This slice deliberately does **not** enable canary routing and does not claim the
runtime complete. Cell-owned preview/database binding, resident runner dispatch,
persistent supervision/browser lifecycle, controlled egress/dependency installation,
and production recovery/isolation proofs remain required exactly as documented above.

## Local draft integration checkpoint (not yet released)

- The API draft adapter now calls workspace-scoped `draft/apply` and
  `draft/preview-session`, carrying generation identity, fencing epoch and source
  revision. Empty patches ensure the draft runtime too. Package/migration failures
  fail closed; generated files and revision refresh from the real cell workspace.
- Runtime checks, logs, visual inspection and authenticated probes no longer
  delegate to the legacy runtime when a cell is selected. Platform docs/media
  services remain reusable. The old email-auth isolation probe is explicitly
  unavailable for MAX cells until two signed identities are implemented.
- Candidate retry/cancellation now enforces active run/lease ownership and
  authoritative locked ORM refresh. Evidence references require content-addressed
  SHA-256 keys, including database backup, build and verification artifacts.
- Linux CI failures in the Windows lock shim and an overlong test name are fixed
  locally. CI on the preceding published revision passed web and image builds;
  this does not establish CI success for the subsequent local diff.
- API executor/client/MAX runtime regression suite: **61 passed in 33.63s**.
  Cell browser probe regression suite: **5 passed in 0.10s**. API Ruff and Mypy
  passed (243 source files). Candidate worker reports **8 candidate tests passed**
  against a separate disposable PostgreSQL database.
- Orchestrator cell draft lifecycle and the messages pipeline's remaining legacy
  bypasses are being integrated in parallel. Full review, migration/release gates,
  commit/push and production deployment remain open. All owner flags stay off.

Cloud limitations observed during this continuation:

- Sequential Thinking MCP and Context7 were not present in the available toolset.
- The checkout has no configured Git remote, so it has no permitted push/PR/release
  channel yet.
- Docker and local PostgreSQL executables are absent. Ruff, Mypy and diff checks can
  run locally; PostgreSQL-backed tests and live Docker proofs require an external CI
  or release runner.

## Local release closure update — 2026-09-02

This section supersedes the earlier local gap statements where they conflict.

- The selected Project Cell path now owns workspace reads/writes, shell/build,
  migration, draft runtime, preview URL, authenticated MAX probe and logs. The
  draft runtime uses the cell workspace volume and its dedicated PostgreSQL and
  Redis; it does not fall back to the legacy runtime.
- Generation-run identity and fencing are checked again at bootstrap and before
  marking the workspace ready. Candidate retries require the same immutable
  content-addressed evidence and an active authoritative lease.
- Draft migration failure is fail-closed. TLS publication failure removes the
  temporary HTTP vhost before returning an error. The persistent draft-port
  registry is schema-validated, crash-safe and atomically replaced.
- Release-critical API + Project Cell regression gate: **357 passed** in
  320.20 seconds. API Ruff passed; Mypy: **243 source files clean**.
- Full orchestrator gate: **653 passed, 5 skipped, 15 xfailed** in 18.06 seconds.
  Orchestrator Ruff passed; Mypy: **52 source files clean**.
- Production Compose policy and Python source compilation passed. `git diff --check`
  is clean except Windows line-ending notices.
- Production is still on `d81295f6`, owner routing is still off, and the exact
  MAX dev template image was last observed missing. Commit/push/CI/deploy/image
  build/health, owner-only enablement and the retained first MAX generation remain
  open and must be completed serially.
- Remaining post-canary product seams are explicit: no resident runner/browser
  supervisor, no controlled dependency-download path, candidate persistence is not
  yet the Project/Snapshot accepted pointer, and generic two-signed-tenant probing
  is unavailable. The first owner canary is restricted to the bundled MAX dependency
  set and must retain all fail-closed gates.

## Local production and HTTPS proof update — 2026-09-02

This section supersedes previous deployment observations above.

- Production API, worker, web and orchestrator are deployed and healthy at
  `de38d465ceea64b3105627b898054548fb740fed` (merged PR #27; all seven CI jobs passed).
  Migration head is `0054_project_cell_candidates`. Owner routing remains OFF.
- Final-source disposable Docker proof passed: dedicated PostgreSQL CRUD, Redis,
  workspace and agent-home persistence, stale write/exec rejection, bounded
  commands, pause/wake, checkpoint restore, and no host mounts/ports.
- A subsequent candidate fixes two real draft blockers: Next 15 Turbopack rejects
  the bundled dependency symlink, and Docker 29 does not activate published ports
  on internal-only networks. Draft now uses Next 15's default Webpack mode; nginx
  targets the verified owned-network private container address without adding egress.
- Candidate HTTPS proof passed on the production host: unsigned API 401, signed
  bootstrap 307, representative frontend 200 and authenticated cell database API
  200. The template intentionally has no root page, so the disposable proof writes
  a representative page through the normal fenced workspace API. All synthetic
  Docker resources and its nginx vhost were removed afterwards.
- Public MAX runtime/start/preview-session now select the durable cell path, with
  no legacy fallback after cell ownership. Unsupported public config edits,
  publication, stop/keepalive and logs fail closed. Sync-kit is read-only for cells.
- Lifecycle ingress synchronization reacquires the workspace operation lock and
  validates both operation identity and fencing epoch; delayed lifecycle replies
  cannot republish or remove newer ingress. Signed query values are redacted in logs.
- This follow-up candidate still needs its own final gates, push and deployment.
  Owner-only enablement and the retained first generation have NOT run yet.
  Do not claim full resident-agent or arbitrary dependency-install support.

Follow-up pre-commit gates: release-critical API and cell suite **360 passed**;
new MAX HTTP/ownership/locking regressions **10 passed**; legacy public/fork
regressions **19 passed** on separate disposable PostgreSQL databases. Full
orchestrator **676 passed, 5 skipped, 15 xfailed**. Ruff is clean in both apps;
Mypy is clean for 244 API and 52 orchestrator source files. Independent review
reports no remaining actionable findings in the complete follow-up slice. Dark
cells also reject public source/legacy redirects/remix and non-owner direct forks.
