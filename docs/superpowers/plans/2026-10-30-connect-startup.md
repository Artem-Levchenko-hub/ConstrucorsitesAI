# CONNECT startup implementation plan

> **For agentic workers:** Use superpowers:executing-plans for this small sequential repair; independent review is retained before delivery.

**Goal:** Make the observed default-Node package transport work while preserving crypto and stream security.
**Architecture:** A CONNECT-only bounded startup writer; unchanged routing/security and a complete portable source-bundle successor.
**Tech stack:** Python standard library, pytest, native Docker canary, canonical release scripts.
**Spec:** `docs/superpowers/specs/2026-10-30-connect-startup-design.md`.

## Global constraints

2048-byte prefix, 384-byte writes, 12 ms spacing, at most 72 ms total intentional
pauses; one 20-second deadline per original write. No TLS/group/CA changes, paid
retries, app-source mutation, business-data mutation or concurrent writers.

## Review focus

Leftover/read boundaries and tiny reads; timeout/pacing leakage; partial errors;
protocol-neutral byte preservation; historical source bundles and proxy lifecycle.

## Task 1: Repair and deliver the transport

- [x] Add a failing sensitive-path handler test plus small-read and partial-timeout
  regressions in `apps/orchestrator/tests/test_machine_egress_startup.py`; observe
  the three stream cases fail on unshaped startup and two missing-writer failures.
- [ ] Implement `_ConnectWriter.sendall(data: bytes) -> None` in
  `services/machine_egress.py`, routing both CONNECT entry paths through it. Extract
  the immediate relay loop to keep routing separate from byte forwarding.
- [ ] Add absolute-deadline, separate-read idle, opaque data and HTTP coverage;
  run egress, analytics relay and same-IP proxy-upgrade tests, ruff and mypy.
- [ ] Preserve the historical 14-source owner bundle/fixture; register a full
  successor and cover exact-current, missing, unknown and cross-product rejection.
- [ ] Probe the actual candidate proxy with default Node and public tarballs;
  no client-side shaping, credentials, app volumes or model calls.
- [ ] Obtain independent review, run the project suite and release contracts,
  commit/push, wait for exact-revision CI, canonical deploy and health/smoke proof.
- [ ] Update reports with actual outcomes and preserve the separate pending MAX
  acceptance; do not turn a nonpaid transport probe into a claimed MAX publication.
