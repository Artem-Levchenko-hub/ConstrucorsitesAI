# Generation recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. The user chose sequential execution in this chat. Steps use checkbox syntax.

**Goal:** Keep useful generation progress and certify only the actual final result.

**Architecture:** Reuse the current executor and MAX coordinator. Continue bounded
text-protocol segments only after actual source progress. Save a private candidate
checkpoint before rollback, with owner/project/run/baseline binding.

**Tech Stack:** Python/FastAPI, PostgreSQL, private MinIO, pytest, Next.js MAX cells.

**Spec:** docs/superpowers/specs/2026-10-12-generation-recovery.md

## Global Constraints

- No blind replay of ambiguous provider POSTs or unsafe migration changes.
- Preserve accepted data, ownership and bot bindings; retain all finalization gates.
- Keep one run/cell for automatic continuation and configured outer deadlines.
- Deliver through the documented production release with exact revision health.

## Review Focus

- A write after a green check must invalidate completion, including shell writes.
- A whole no-progress segment must not buy another paid model segment.
- A provider error or cancellation must not restart from the beginning.
- A saved candidate from a different owner or baseline must never resume.
- Rollback becoming green must never mark the failed candidate completed.

### Task 1: Protocol and current-state completion

**Files:** services/agent_builder.py, tests/test_agent_builder.py (under apps/api).
**Interfaces:** Preserve Action and AgentResult; run_agent_build keeps existing defaults.

- [x] Add tests for known alternate action tags and reject non-object arguments.
- [x] Reproduce stale-green completion and repeated premature done; observe RED.
- [x] Fix parsing and fresh-check enforcement; remove invented component nudges.
- [x] Run builder tests, Ruff and mypy; expect PASS, then commit intended changes.

### Task 2: Same-run continuation and private recovery checkpoint

**Files:** agent_builder.py; generation/agent_generation.py; generation/agent_pipeline.py;
generation/agent_preparation.py; new generation/source_checkpoint.py; associated tests.
**Interfaces:** Add max_segments to the text loop, default 1; persist checkpoint by UUID identity.

- [x] Reproduce a last-step write followed by successful next-segment verification.
- [x] Test no-progress/provider/cancel stops and exact cumulative files; observe RED.
- [x] Implement progress-bound segments, per-segment guidance and checkpoint persistence.
- [x] Test owner/baseline mismatch and explicit adjacent continuation; observe RED then GREEN.
- [ ] Run affected tests and full API suite on isolated services; expect PASS; commit.

### Task 3: Diagnostic and final-result contracts

**Files:** gateway provider failure classification and API result mapping where needed;
existing MAX completion/finalization tests and generation progress tests.
**Interfaces:** Only safe refusal categories cross API boundaries; no raw secrets.

- [x] Reproduce missing component/invalid page export/schema/UI contract errors through existing repair gates.
- [x] Verify refusal categories distinguish access/funds/rate without pretending ambiguous charges are free.
- [x] Fix only remaining gaps, with regression tests RED then GREEN; preserve acceptance gates.
- [x] Run API/gateway checks and independent branch review; fix material findings; commit.

### Task 4: Delivery and live acceptance

**Files:** docs/qa/2026-10-12-generation-recovery.md and delivery evidence outside repository.
**Interfaces:** Exact pushed release SHA; existing release scripts and stable app publication.

- [ ] Push branch, create/attach PR, require full CI, merge within existing authorization.
- [ ] Deploy serially by documented production route; verify API/workers/controllers/gateway.
- [x] Check provider availability without payment; execute authorized generation pairs only if available.
- [ ] Check final UI/API/MAX behavior and data persistence, record actual accepted rounds or external blocker.
- [x] Publish a concise checkpoint with revision, tests, live evidence and remaining limitations.

Provider check: authenticated balance -16.02 RUB on 2026-10-12; no new paid calls.
Delivery remains pending full CI and recovery of the prior 89a73036 release fence.
