# Yleum Support Tickets Implementation Plan

> Implement independent API/web file sets against this contract; primary owns
> integration, migration, Git and deployment. Use TDD and a final fresh read-only
> whole-branch review. The user directly authorized implementation and delivery.

**Goal:** Permanent support entry, durable conversations, fast notifications to
all administrators and a three-state queue with a 24-hour archive.
**Architecture:** PostgreSQL tickets/messages/support outbox, separate authorized
support WebSocket channels, general-worker mail loop, existing branded SMTP.
**Stack:** Existing locked FastAPI/SQLAlchemy/Redis and Next/React/React Query.
**Spec:** `docs/superpowers/specs/2026-10-26-support-tickets-design.md`.

## Global constraints and review focus

Preserve unrelated dirty work; no new vendor/dependency, AI calls or payment.
All active persisted/allowlisted admins must be covered. Guest email is not
account authority. Capabilities stay out of URLs/logs. SMTP outages must not
reject committed tickets; ambiguous sends must not be replayed blindly. Derived
archive remains correct when workers stop. User and admin session revocation,
reconnect, retries and concurrent status/reply updates must be covered.

## Task 1 — Domain, API, realtime and email

Owner: backend implementer. Files: new `models/support.py`, `schemas/support.py`,
`services/support_tickets.py`, `services/support_email.py`,
`services/support_realtime.py`, `routers/support.py`, focused `tests/test_support*`;
extend `services/email_templates.py` for support messages only.

- [ ] Add meaningful failing tests for guest/owner/admin boundaries, all-admin
  fanout, duplicates/rollback and archive/reopen; observe RED before implementation.
- [ ] Implement the exact HTTP/WS DTOs in the spec, transactional thread locking,
  durable rate admission, safe event fanout and origin/session revalidation.
- [ ] Implement support outbox claim/retry/uncertain handling and branded emails;
  prove failure paths and concurrent claims in disposable PostgreSQL.
- [ ] Run focused API tests and changed-file Ruff/mypy; report exact results and
  any integration signatures to primary. Do not mutate Git or migration files.

## Task 2 — Customer and administrator UI

Owner: frontend implementer. Files: `lib/api/support.ts`, `hooks/useSupportEvents.ts`,
new `components/support/*`, `components/account/AdminSupportPanel.tsx` and settings
archive panel; integrate `app/providers.tsx`, `AdminControlCenter.tsx`, AccountShell
and scoped CSS; focused web tests.

- [ ] Add failing tests for permanent launcher, guest/registered submission,
  retained draft and retry ID, thread reply/reconnect and admin deep links.
- [ ] Implement native Yleum UI, accessible portal, safe areas/mobile scroll,
  guest capability storage and private-cache clearing on account switch.
- [ ] Add all-admin toasts/count invalidation, status actions with conflict
  recovery, Settings -> archive, search/pagination and customer replies.
- [ ] Run focused tests/typecheck/lint; coordinate contracts with backend. Do not
  mutate Git, lockfiles, API or deployment files.

## Task 3 — Integration and reproducible verification

Owner: primary. Files: additive migration, `models/__init__.py`, `main.py`,
`workers/run.py`, minimal config/production compose for general-worker SMTP,
integration tests and report/checkpoint.

- [ ] Confirm head and create the additive migration from backend models; verify
  upgrade and no existing data/schema contract loss on disposable PostgreSQL.
- [ ] Register router/hub and worker loop using proper lifecycle cleanup and a
  thread-local SQL engine; expose SMTP to the existing trusted general worker.
- [ ] Reuse dependency caches while isolating node caches/ports/DB/output. Run
  focused vertical slice, auth/transport/admin/mail adjacent regression checks.
- [ ] Independently review the entire diff and surrounding contracts. Fix P0/P1/P2
  and material P3, rerun affected checks, then required full lint/types/build gate.

## Task 4 — Delivery and live acceptance

- [ ] Primary stages only intended files, commits clearly and pushes the branch's
  configured upstream. Preserve concurrent origin/main changes without force.
- [ ] Wait for green full CI of the exact pushed SHA. Use only documented
  `infra/release/deploy-prod.sh` and production compose/project, with quiescence,
  release ownership, backup, reader inventory and health proof.
- [ ] Prove current revision/service health and a controlled live support request,
  administrator notification/reply, refresh/reconnect and 390px flow. Record email
  acceptance separately from inbox proof and do not claim untested scenarios.
- [ ] Publish concise acceptance evidence and exact remaining blockers, if any.
