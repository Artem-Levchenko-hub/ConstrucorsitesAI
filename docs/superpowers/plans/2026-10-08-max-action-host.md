# MAX Server Actions forwarding implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore generated Next Server Actions behind the trusted gateway without trusting client forwarding headers or weakening CSRF protection.

**Architecture:** The controller supplies the exact preview/public HTTPS origin. The gateway supplies that authority as upstream Host and X-Forwarded-Host, strips caller forwarding credentials, and preserves Origin for the existing checks.

**Tech Stack:** Python HTTP gateway, pytest, Next.js 15.5.24.

**Spec:** User acceptance request: four pairs of concurrent generations. Coffee is published through the existing MAX bot and checked with retained business data; fitness is tested without a bot. Live service-web-17.log shows internal forwarded host failing Next Server Actions.

## Global constraints

- Preserve existing business data, signed identity, credential stripping and public same-origin checks.
- Use controller configuration, never request Origin or Host, to choose upstream authority.
- Deliver through reviewed commit, CI, canonical production deploy and live acceptance.

## Review focus

- Forged Host and forwarding headers must not affect the selected authority.
- A foreign Origin must remain foreign so Next cannot mistake it for same-origin.
- Missing or malformed configured origins must not become HTTP headers.
- Public embedded sessions must retain the existing CSRF rejection behavior.
- Preview and published configurations must both supply their real origin.

## Task 1: Gateway authority

**Files:** `services/machine_boundary.py`, `services/machine_adapter.py`, `tests/test_boundary_action_host.py`, preview controller fixtures under `apps/orchestrator`.

- [x] Add actual HTTP regression covering preview/public POST authority with forged forwarding headers and foreign Origin.
- [x] Run it against baseline and observe expected assertion failure.
- [x] Supply controller-derived preview origin and upstream canonical Host/forwarded headers for product requests.
- [x] Run gateway/security tests, the orchestrator suite, lint and type checks; request independent review.
- [ ] Commit/push, pass CI, back up and deploy canonically, reproduce the browser operation.

## Acceptance ledger

Initial state: no code changes, zero accepted rounds. Existing coffee bot reuse authorized; old URL preserved in local checkpoint. Token copy from MAX browser returns an empty clipboard; direct UI paste requested. Separate stale compilation observation remains under investigation.

Targeted run: 117 passed, 14 skipped. Independent review: clean, including fixture changes. Full scratch run initially had 25 failures and 5 errors: missing Node/PostgreSQL services plus two additional preview fixtures lacking an explicit origin resolver. Scratch now uses Node 22.20.0 and separate loopback-only PostgreSQL 16 containers; no production database is used by these tests.

Pre-deploy encrypted backup: `20261008-063957`, stored in canonical MinIO and copied encrypted to commerce. SHA-256 `879be5aa7397f03d37863b0a3d1e6bb8f4847cbd0ef71e2b65937cdbb4a57f7d`; source and peer checks pass. This does not claim a restoration test.

Final isolated orchestrator suite: 2881 passed, 62 skipped in 361.73 seconds. Ruff passes; mypy reports no issues in 113 source files. Optional live Docker probes remain for the CI gate. Real Next/browser acceptance remains open until the deployed controller refreshes the existing gateway.
