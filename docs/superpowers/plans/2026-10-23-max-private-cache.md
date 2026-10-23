# MAX private response cache policy implementation plan

> **For agentic workers:** Use superpowers:executing-plans for this bounded fix. User has authorized fixes through deployment and live verification.

**Goal:** Prevent HTTP caches from storing personal MAX miniapp responses, including generated routes that omit or override cache policy.

**Architecture:** Apply one authoritative `Cache-Control: private, no-store` at the trusted machine boundary for authenticated product responses and session bootstrap endpoints. Preserve only trusted managed-core static asset caching, response identity checks, Vary and ETag. Do not change project data, ownership, SQL policies or legal confirmations.

**Tech stack:** Python HTTP boundary, pytest real loopback HTTP, current production orchestrator and supported publication config refresh.

**Spec/evidence:** Existing isolation request; external `qa-evidence/actor-isolation-20261023/final-read-check.json` reproduces authenticated Notes HTTP 200 without Cache-Control. RFC 9111 sections 5.2.2.5 and 7.3 explain cache semantics. This is a missing policy, not an observed live cross-user leak.

## Constraints and review focus

- Preserve existing authenticated user/project/epoch and origin validation.
- Suppress upstream duplicate/public cache policy and CDN cache overrides; preserve Vary and ETag/If-Match semantics.
- Include API, HTML/RSC, downloads, errors and HEAD; do not infer public safety from file extension or a generic `/assets` prefix.
- Preserve managed core static assets only. Generated `/_next/static/` can be a personalized rewrite and is not trusted evidence of static content.
- Existing healthy published runtimes need an explicit supported config refresh; platform health alone does not prove their boundary was updated.

## Task 1 — policy and regression proof

Files: `apps/orchestrator/services/machine_boundary.py` (under `src/yleum_orchestrator`), new `apps/orchestrator/tests/test_machine_boundary_cache.py`.

- [x] Add actual HTTP regression tests for missing/public/duplicate upstream policy, different actors, private HTML/API/download responses, HEAD/errors, session bootstrap and trusted-core static preservation.
- [x] Run tests against unchanged product code and retain the expected failures: 12 failed, 3 passed; the additional static rewrite regression also failed before its correction.
- [x] Extract a small response policy helper and use it in `_forward`; replace conflicting cache directives without changing unrelated forwarding.
- [x] Run focused boundary/session HTTP tests: 106 passed, 11 browser cases deselected after their missing-Chromium environment failure was recorded; final 15 policy tests passed after the review correction. Ruff and touched-file mypy pass.
- [x] Obtain an independent review; fix its static-prefix P2 with RED/GREEN. Final re-review found no remaining P1/P2.

## Task 2 — delivery and live proof

- [ ] Commit intended files, integrate to main without rewriting history, push and wait for the exact main CI result.
- [ ] Use the unchanged canonical production driver once for the approved SHA; retain the durable deployment record and confirm service health.
- [ ] Refresh only the two authorized QA publications through the supported owner config path, preserving release/data/configuration. Confirm the new boundary source/pod and personal response headers.
- [ ] Repeat the two synthetic actor isolation matrices after rollout. Confirm all temporary business rows are removed, and update the Russian HTML report/checklist without changing stored user marks.

## Ledger

- Baseline d7840695: Notes and Tasks own CRUD, symmetric cross-owner write denial, owner spoofing and concurrent reads passed with synthetic signed actors. No real second MAX account was used, as requested.
- Ruling: use an external manual worktree because native create_worktree returned `Git is unavailable`; primary checkout stays intact.
- Ruling: permission for deployment and supported QA runtime refresh follows the user's explicit request to fix findings and carry them to production; no repeated approval is needed.
- Independent review found the generated static-prefix exception unsafe; a real HTTP regression reproduced two personalized bodies under that path with public cache policy. Remove that exception; accept extra network loads for generated assets until a controller-verified static asset contract exists.
