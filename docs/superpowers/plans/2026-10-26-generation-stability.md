# Generation retry context and verified source progress — implementation plan

> For agentic workers: use superpowers:executing-plans for the root-owned loop change and bounded independent review for the request-source resolver. Steps use checkboxes.

**Goal:** Preserve the admitted visual/font task across explicit manual retries and prevent an unchanged application from being handed off as a completed modification.

**Architecture:** Resolve one verified source message for the recovered text, images and fonts. Separately compare the actual workspace against accepted source before allowing either agent loop to claim a required modification. Keep final acceptance, ownership, fences, deadlines, billing and paid-call guards.

**Tech stack:** Python, SQLAlchemy, pytest, existing generation runners and canonical release driver.

**Spec/evidence:** `qa-evidence/generation-stability-20261026/forensic-conclusion.json` outside Git; project c56a7913-a6f3-4b0e-b72b-a22860097f24, base 28c776536302a5e53caac46c99f96259ad0abe2f. Eleven retained legacy responses had exactly one action each; no write actions or discarded code. Four shell actions were reads/searches only. Latest two runs failed final no-change validation. The earlier image-bearing request failed environment reconciliation; subsequent manual retry messages had no reference binding.

## Global constraints

- No automatic paid replay or budget increase; no weakening final source validation.
- Preserve project data, existing accepted versions, scoped identities and original admission hashes.
- A manual retry may recover only an adjacent owned failed/cancelled build/edit chain with a compatible accepted baseline; completed, active, chat, foreign, altered or ambiguous records are barriers.
- Existing explicit checkpoint provenance remains authoritative; do not interpret recovered attachments as permission to restore unchecked source.
- Cold resume rechecks actual source, never trusts historical green/source-changed flags.
- Current diagnosis distinguishes proven retained responses from unavailable controller subprocess exit codes; no invented permission or rollback failure.

## Review focus

- Cross-owner/project or changed-baseline inheritance must fail closed.
- New text/attachments must not inherit an older task implicitly.
- No-op writes, documentation, unchanged full exports and reverting to baseline do not establish required product changes.
- Real file/shell changes and already-restored changed drafts must still complete normally.
- Explicit read-only requests must not be forced to write.

### Task 1: Verified manual-retry request source

Files: shared resolver under `services/generation/`; callers `agent_preparation.py`, `agent_pipeline.py`, `agent_seed.py`; focused request/image/font tests.

- [x] Reproduce a visual failure before checkpoint followed by one/two plain retries; the old code loses image bytes.
- [x] Resolve and validate the same original request for text, images and fonts without rewriting dispatch or messages.
- [x] Test own replacement attachments, completed/active/chat barriers, foreign identity, request tampering, baseline changes, equal timestamps, chain bound and cold resume.
- [x] Verify existing checkpoint/image/font contracts remain intact; independent review.

### Task 2: Required real source progress in legacy handoff

Files: `agent_builder.py`, `generation/agent_generation.py`, and related edit/source-progress tests; retain existing native guard behavior.

- [x] Reproduce the exact read/bash/bash/done/build/done sequence returning success with an unchanged workspace.
- [x] Share actual workspace observation with legacy execution and initialize it before first action for restored drafts.
- [x] Gate verification, done, green-abort promotion and guidance on required actual changes, using the existing finite no-progress budget (14 in legacy; unchanged).
- [x] Cover real writes, shell writes, unchanged/forged exports, revert, repeated done/build and read-only behavior; rerun related suites.

### Task 3: Review and delivery

- [x] Investigate the original cancelled-cell reconciliation records. Add bounded waiting for the previous RUNNING ensure/reconcile before the existing reclaimer, without keeping DB locks, changing old fences, or resetting the original capacity deadline. Seventeen deterministic tests pass.
- [ ] Run relevant local checks and exact-revision Linux CI, obtain independent review, commit and push.
- [ ] Deploy only the documented canonical production driver once; confirm exact release health and preservation.
- [ ] Verify the real scoped retry-message resolver against preserved metadata and run source-progress acceptance without paying for an unnecessary model replay.
- [ ] Report exact causes, shipped revision, acceptance evidence and remaining external/provider limits; update the weekly report's incident note.

## Baseline

- Native worktree tool failed with Git unavailable; isolated manual worktree created at `.worktrees/generation-stability-20261026` outside the main checkout.
- Local non-DB related baseline: 142 passed. Initial QA environment lacked python-multipart and test settings; the locked dependency was installed in the private QA virtualenv and isolated dummy settings were supplied. These setup errors are not product regressions.
- Native Docker/WSL/PostgreSQL are unavailable on this Windows host; do not point destructive test fixtures at production. Use local real-loop reproductions and Linux/isolated PostgreSQL integration evidence.
- Independent review completed for all three tasks. Scoped data-preservation and polite/analysis request regressions were reproduced and corrected. Existing terminal-agent results skip observer setup. Final review has no actionable P1/P2 findings.
- Full related local test run initially found three regressions and one unavailable-PostgreSQL setup error; the three code regressions were fixed, with 180 affected tests passing on repeat. The real PostgreSQL attachment integration remains required in exact-revision Linux CI. Full related non-PostgreSQL recheck is recorded separately in the delivery evidence.
- Changed-file Ruff and mypy for all nine affected source modules pass. No paid provider replay or live project-source mutation has been made for this change.
- Final combined related local run: **456 passed, 1 deselected** (the one real PostgreSQL integration), 101.07 seconds. Logs are outside Git in `qa-evidence/generation-stability-20261026/combined-final-v2.log`; CI runs the PostgreSQL case on disposable services.
- First CI (`108101a7`, run `40283722656`) exposed eight budget-test fixture failures: its fake executor did not expose actual source reads. Bind its existing snapshot reader to the executor's export/snapshot methods; retain every budget/call-count assertion. The affected budget/coffee suites pass all 49 cases; independent review found no P1/P2. No production source changed in this follow-up.
