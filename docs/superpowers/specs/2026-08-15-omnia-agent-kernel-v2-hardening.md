# Omnia Agent Kernel v2: repository adoption and hardening

## Decision

Keep Omnia's native agent as the only task owner. Adopt four small, read-only
analysis components inside the existing deterministic `build` and release-proof
path. Do not import another autonomous loop, orchestration framework or code
rewriter. This gives the model earlier factual errors without another schema,
provider turn or competing memory.

## Adopted repositories

| Repository | Pin | License | Role in Omnia | Expected effect | Runtime/maintenance cost |
| --- | --- | --- | --- | --- | --- |
| [oxc-project/oxc](https://github.com/oxc-project/oxc) (`oxlint`) | `1.78.0` | MIT | Fast JS/TS correctness lint in every enriched build | Finds invalid and suspicious code before the next model turn | One pinned dev dependency; bounded child process |
| [ast-grep/ast-grep](https://github.com/ast-grep/ast-grep) | `0.45.1` | MIT | Optional AST rule/pattern checks without rewriting | Lets Omnia add precise MAX-specific checks with low text false-positive rate | Inert until a managed rule/pattern is supplied |
| [sverweij/dependency-cruiser](https://github.com/sverweij/dependency-cruiser) | `18.2.0` | MIT | Detect circular and unresolved imports under `src/` | Moves import-graph failures into the same repair observation | Managed config required; excludes `node_modules` and aliases correctly |
| [google/osv-scanner](https://github.com/google/osv-scanner) | `2.5.0` | Apache-2.0 | Scan the pinned `pnpm-lock.yaml` at release proof | Prevents a known vulnerable dependency graph from being published | Pinned binary + SHA-256 per architecture; network/API availability is a release dependency |

The version and license inventory is explicit so upgrades are reviewed, not
silently pulled through `latest`. The Docker build verifies OSV-Scanner's release
checksum before installation. None of these tools receives source code from
Omnia. Oxlint, ast-grep and dependency-cruiser run locally. Online OSV requests
contain dependency package names, ecosystems and versions from the lockfile; an
offline OSV database can replace that later if this metadata boundary becomes
unacceptable.

## Repositories assessed but not imported

| Family / examples | Why it is not in this slice |
| --- | --- |
| Autonomous coding loops: [block/goose](https://github.com/block/goose), [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands), [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent), [Aider-AI/aider](https://github.com/Aider-AI/aider) | A second planner/tool loop would duplicate ownership, checkpoints, billing and retries. Goose remains a possible later bounded specialist only. |
| Agent orchestration/memory: LangGraph, AutoGen, CrewAI, OpenAI Agents SDK | Omnia already has the required durable run, lease, transcript, plan and provider gateway. Replacing the control plane is high-risk and does not directly improve the current MAX failure modes. |
| Overlapping linters: Biome, ESLint-only expansion, Semgrep | Oxlint plus targeted ast-grep covers the immediate TypeScript/template signal with less image and rule sprawl. Semgrep can be reconsidered only for a proven security-rule gap. |
| Heavy quality servers: SonarQube/SonarCloud | Adds a service, project synchronization and external-state dependency to a per-project inner loop. Better suited to repository CI, not every paid agent iteration. |
| Restricted/non-production-friendly analysis: CodeQL CLI bundles | Licensing and operating model are less suitable for transparent embedding in generated customer projects. GitHub code scanning remains an optional external CI layer. |

“Assessed” means architecture, license fit, overlap and integration cost were
compared for this concrete failure pattern. It is not a claim that every GitHub
repository was enumerated; GitHub is unbounded and changes continuously.

## Failure and recovery contracts

1. A disabled/legacy `build` is TypeScript-only. On an enabled canary, analyzer
   errors make the enriched build red; analyzer absence, timeout or malformed
   output is recorded in `unavailable` and fails soft.
2. The release dependency-security check is fail-closed for enabled canaries.
   OSV completion and findings use dedicated bounded fields, independent of the
   general diagnostics cap.
3. Child commands have fixed argv, no shell, bounded output and a 60-second tool
   timeout; the orchestrator applies an outer deadline too.
4. Structured paths are normalized and capped. The repair allowlist accepts only
   model-owned MAX source paths; diagnostics cannot unlock managed files.
5. Structured tool JSON is capped while remaining valid JSON.
6. Assistant tool-use, a per-action `started` marker and each tool result are
   journalled. Resume uses a stored assistant response without another provider
   call, skips completed actions and reconciles only the started action lacking a
   result. Mutating shell and paid-media actions are conservatively at-most-once:
   recovery never repeats them. A pre-journal ambiguous response still reuses the
   same logical gateway turn. MAX shell accepts only exact platform-owned
   read-only checks with an empty mutation list; every free-form or mutating
   command is rejected, so binary source and installed state never enter a lossy
   shell rollback path.
7. Infrastructure failure does not turn the last compiler state red and does not
   force a source mutation. A repeated semantic error requires a new falsifiable
   diagnosis and stops after three failed experiments.
8. Public plan completion accepts only server-issued compatible evidence IDs from
   the current workspace revision. Source mutation reopens build/runtime/visual
   steps, and a later red proof supersedes an older green result.

## Rollout and measurable effect

Both global flags default to false. Enable only the owner allowlist first. Rebuild
the managed template for new projects; do not destroy existing project containers.
Compare canary with the prior kernel on factual server metrics:

- provider turns and build calls per successful run;
- repeated normalized error signatures per run;
- continuation/semantic-loop stop counts;
- percentage reaching `contract_green` without owner intervention;
- analyzer time, unavailable rate and false-positive repairs;
- vulnerabilities blocked at release.

No percentage improvement is claimed before this canary evidence exists. Success
means fewer paid turns and repeated repairs without lowering the existing signed
runtime, visual, persistence, isolation or release gates.

Rollback requires no data migration: clear `MAX_CODE_INTELLIGENCE_CANARY_USERS`,
keep `MAX_CODE_INTELLIGENCE_ENABLED=false`, recreate API and worker, and leave the
last good published snapshot untouched.
