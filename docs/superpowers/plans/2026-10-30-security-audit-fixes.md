# Security audit corrections implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Keep Git and deployment serial.

**Goal:** Deliver all five audited security corrections while preserving data.
**Architecture:** Reconcile archived obsolete QA containers without weakening backup
census; shared HTTP/WS Origin policy; per-flow OAuth browser binding; reader-first
password-format rollout using existing Passlib.
**Tech Stack:** Python/FastAPI, PostgreSQL, Next.js, Docker, existing backup tooling.
**Spec:** `docs/superpowers/specs/2026-10-30-security-audit-fixes-design.md`

## Global constraints

Backup first. Never restore into a customer database/volume. Preserve all old
copies. No new paid generation or outgoing email. Commit/push all intended changes,
require exact CI and deploy only through documented production scripts. Independent
review uses gpt-6.1-sol with reasoning max. Preserve unrelated workspace changes.

## Review focus

- Cookie plus Bearer still uses the cookie and must not bypass Origin validation.
- Web server actions and RSC OAuth pending reads must forward required cookies.
- OAuth alias hosts and simultaneous starts must not invalidate legitimate flows.
- No automatic long-legacy-password suffix enrollment or unsupported hash rollback.
- A partial backup, envelope-only verification or untested restore is not full DR.

### Task 1 — Full backup recovery

- [ ] Capture exact stale identities and reproduce missing source resolution.
- [ ] Archive five stopped QA filesystems, metadata and logs; verify checksums and
      archive membership before exact identity/state-guarded removal (no `-v`).
- [ ] Run canonical full backup, require complete cells/peer coverage, then
      `infra/backup/restore-test-omnia.sh` against new scratch resources only.
- [ ] Record receipts and any offline-key limitation. Do not change backup guards.

### Task 2 — HTTP and WebSocket Origin enforcement

Files: add `apps/api/src/yleum_api/core/request_origin.py`; modify `main.py`,
`routers/ws.py`, directly related existing Origin helpers and web auth actions.
Tests: extend `apps/api/tests/test_auth_transport_security.py` and
`apps/web/src/lib/__tests__/auth-session-forwarding.test.ts`.

- [ ] RED: trusted-origin positive controls; missing/null/sibling/lookalike negatives;
      cookie+Bearer denial; safe methods and cookie-free Bearer compatibility;
      WS cookie vs explicit-token behavior, owner denial and revocation retained.
- [ ] GREEN: shared exact policy and body-preserving ASGI guard; web Origin forwarding.
- [ ] Run targeted API/web checks; refactor touched helpers; review resulting diff.

### Task 3 — OAuth browser binding

Files: `routers/auth_oauth.py`, small binding helper, web OAuth helpers/auth actions
and canonical auth entry. Tests: `test_oauth_login.py`, `oauth-login.test.ts`,
`oauth-complete-action.test.ts`, `oauth-buttons.test.tsx` and existing consent tests.

- [ ] RED: foreign browser cannot exchange/consume state or access/spend pending
      ticket; own browser succeeds; two starts finish in reverse order; expiry,
      replay, swapped-purpose cookies, canonical aliases and local development.
- [ ] GREEN: per-state/per-ticket host-only production __Host cookies with separate
      purposes; selective forwarding/deletion; preserve PKCE, locking and legal flow.
- [ ] Run relevant API and web suites and inspect cookies for secure attributes.

### Task 4 — Password reader compatibility release

Files: `core/security.py`, `tests/test_password_hashing.py`; deployment notes.

- [ ] RED: dual verification accepts existing bcrypt and bcrypt_sha256 hashes.
- [ ] GREEN: add dual reader while retaining legacy default writer temporarily.
- [ ] Include Tasks 2–3 and reviewed docs in a tested compatibility revision;
      commit/push, require exact CI, canonical deploy and live acceptance. This
      becomes the minimum password-compatible rollback target.

### Task 5 — Full-password writes and final acceptance

Files: `core/security.py`, password tests and relevant auth/reset/change tests.

- [ ] RED: new ASCII, Unicode, byte-72-boundary and 128-character passwords reject
      altered suffix; legacy compatibility/recovery follows the recorded policy.
- [ ] GREEN: default writer bcrypt_sha256 v2; enforce safe legacy policy with clear
      recovery behavior; preserve password-reset token/session/notice semantics.
- [ ] Run required full API/web verification, lint/types, independent review;
      fix review findings with regression tests, commit/push, exact CI and deploy.
- [ ] Confirm live health, safe Origin/OAuth controls and synthetic in-memory password
      checks. Record revisions, backup/restore receipts and preserved data evidence.
