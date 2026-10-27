# Green SMS Registration Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans, with one independent whole-branch review.

**Goal:** Deliver registration phone codes through Green SMS without changing subsequent email/password access.

**Architecture:** A provider-neutral facade selects one configured transport. Green SMS receives one HTTPS POST with a private Bearer token; the existing durable OTP and cost-admission state machine remains authoritative.

**Tech Stack:** FastAPI, httpx, Pydantic Settings, PostgreSQL, Next.js, pytest/Vitest.

**Spec:** docs/superpowers/specs/2026-10-27-greensms-registration-design.md

## Global Constraints

- No automatic paid-send retry or fallback; no credentials/OTP in URLs, logs, Git or screenshots.
- Confirmation once; email proof optional after phone proof; later login/recovery retain their current contracts.
- Destructive tests only on disposable PostgreSQL; all live requests bounded and recipient-authorized.
- Full repository delivery loop; production gate only after provider readiness.

## Review Focus

- Green SMS rejects an inactive account, invalid sender or token: fail explicitly, retain quota claim, do not verify it.
- A response is lost after acceptance: preserve the challenge and allow its valid code, send once.
- Account status serializers select exactly the same provider as the send endpoint.
- Separate user/number/IP/global quotas and resend cannot be bypassed with another account or request.
- Verified phone survives logout and email password recovery without another SMS or email-proof requirement.

### Task 1: Transport and selection

Files: services/sms_delivery.py, services/greensms.py, services/smsaero.py, core/config.py,
services/phone_verification.py, schemas/user.py, routers/max_accounts.py,
tests/test_greensms.py and existing phone unit tests (all under apps/api).

- [x] Add failing transport tests for bearer/form/hidden, success request_id, explicit rejection, invalid receipts, 5xx, redirects and timeout; assert one request and redacted exceptions.
- [x] Watch failures against absent provider/selector, then implement the small adapter/facade and update readiness consumers together.
- [x] Run focused unit tests, Ruff and mypy; review the touched functions and immediate callers.

### Task 2: Authentication regressions and user-facing disclosure

Files: apps/api/tests/test_phone_verification_db.py, tests/test_max_account.py;
apps/web/src/components/max/PhoneVerification.tsx and current legal pages;
production compose and env example.

- [x] Add provider-rejection, each-budget, resend-invalidation, same-number no-send and verified-phone login/recovery regression tests.
- [x] Update provider-neutral UI copy and Green SMS processor disclosure with appropriate document revision handling.
- [x] Add production API-only token/sender/selection environment variables; blank credentials in workers.
- [ ] Run focused frontend checks and disposable PostgreSQL regressions in CI, then full required CI.

### Task 3: Delivery and live evidence

- [ ] Independent review; address important findings with RED→GREEN coverage.
- [ ] Commit/push PR, attach it, green exact-head checks, merge and green main checks.
- [ ] Install the privately held token with no secret output; canonical deploy exact green main revision including changed web.
- [ ] Confirm health, selected readiness, Green SMS account/sender requirements and one recipient-approved SMS registration confirmation when available.
- [ ] Record clear passed/blocked results and screenshots; retain old edit acceptance evidence without unnecessary paid generations.

## Execution record

- Work uses the existing clean linked checkout on branch feat/greensms-registration-20261027.
- Transport tests first failed because the provider facade did not exist. Final focused unit run: 45 passed. Scoped Ruff and mypy passed. Frontend focused run: 80 tests / 6 files passed; typecheck passed. Initial unbounded frontend worker startup was interrupted under local resource pressure; the bounded two-worker run passed.
- API PostgreSQL regressions are reserved for the CI disposable service, never the production database.
- Independent whole-diff review: no Critical or Important findings. Conditional Minor retained: the explicit legacy Aero option requires matching UI/legal disclosure before any future use. Intended production selection is Green SMS only.
- Public disclosure is revised to 27 October; exact previous 26 October pages are archived. Canonical release must use --legal-version 2026-10-27 and include web.
- Read-only provider probe accepted the existing private token (HTTP200), zero SMS. Yleum sender awaits moderation. Approved activation ticket4834 was sent through the provider UI with the developer contact. User authorized exactly one live SMS to their stated number; no sends yet.
- Provider activation and live acceptance remain external prerequisites for PHONE_VERIFICATION_ENABLED=true.
