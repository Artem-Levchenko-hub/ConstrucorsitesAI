# Remove Telegram Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Remove both Omnia-to-Telegram generation reporting mechanisms from code and production while preserving generation canaries, unrelated release hardening, and a safe rollback path.

**Architecture:** Delete the observer and daily delivery surfaces, then advance Alembic with a contract migration that removes the observer-only table. A repository-level negative contract prevents reporting code or configuration from returning. Production deploys with the existing observer disabled, proves exact health and build/edit canaries, then purges active server and GitHub secrets by name.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, PostgreSQL 16, pytest, Docker Compose, Bash, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-08-22-remove-telegram-reporting-design.md

## Global Constraints

- Remove both the daily canary Telegram report and the per-generation observer.
- Keep migration 0047 unchanged as immutable Alembic history.
- Keep the build/edit canary, incident lifecycle, and redacted result artifact.
- Keep Telegram-token redaction and Telegram project templates.
- Keep unrelated 7f2c6b6e hardening.
- Never print Telegram secret values; verify and delete only by key name.
- Never stamp or downgrade canonical production data.
- Follow AGENTS.md through verification, push, merge, deploy, and exact health proof.

---

### Task 1: Add a permanent negative repository contract

**Files:**
- Create: apps/api/tests/test_telegram_reporting_removed.py
- Modify: .github/workflows/ci.yml

**Interfaces:**
- Consumes: repository paths and production configuration text.
- Produces: a CI test that rejects reporting code while allowing immutable migrations, generic token redaction, and Telegram project templates.

- [ ] **Step 1: Write the failing test**

    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[3]
    REMOVED_FILES = (
        "apps/api/scripts/dev_generation_telegram_acceptance.py",
        "apps/api/src/omnia_api/models/generation_telegram_report.py",
        "apps/api/src/omnia_api/services/generation_telegram_delivery.py",
        "apps/api/src/omnia_api/services/generation_telegram_reports.py",
        "apps/api/src/omnia_api/workers/generation_reports.py",
        "infra/monitoring/telegram_generation_report.py",
    )
    SURFACES = (
        ".github/workflows/production-generation-canary.yml",
        "apps/llm-gateway/deploy/full/docker-compose.yml",
        "infra/.env.example",
        "infra/ci/README.md",
    )

    def test_reporting_modules_are_absent() -> None:
        assert not [name for name in REMOVED_FILES if (ROOT / name).exists()]

    def test_production_surfaces_have_no_reporting_identifiers() -> None:
        forbidden = (
            "generation-report-worker",
            "DEV_GENERATION_TELEGRAM_REPORTS",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "telegram_generation_report.py",
        )
        for name in SURFACES:
            text = (ROOT / name).read_text(encoding="utf-8")
            assert not [token for token in forbidden if token in text], name

- [ ] **Step 2: Verify RED**

Run from apps/api with CI DATABASE_URL and JWT_SECRET:

    uv run pytest -q tests/test_telegram_reporting_removed.py

Expected: FAIL because reporting modules and identifiers still exist.

- [ ] **Step 3: Add the test to api-release-gate**

Add tests/test_telegram_reporting_removed.py to the API pytest command without removing current tests yet.

- [ ] **Step 4: Commit the red contract**

    git add -- apps/api/tests/test_telegram_reporting_removed.py .github/workflows/ci.yml
    git commit -m "test: forbid Telegram generation reporting"

---

### Task 2: Add migration 0048 to delete observer state

**Files:**
- Create: apps/api/migrations/versions/0048_remove_generation_telegram_reports.py
- Create: apps/api/tests/test_generation_telegram_removal_migration.py
- Modify: infra/release/local-release-gate.sh
- Keep unchanged: apps/api/migrations/versions/0047_generation_telegram_reports.py

**Interfaces:**
- Consumes: revision 0047_generation_telegram_reports.
- Produces: revision 0048_remove_telegram_reports with reversible empty-schema downgrade.

- [ ] **Step 1: Write failing PostgreSQL migration tests**

Create a function-scoped database named with uuid4, point DATABASE_URL at it,
clear get_settings cache, and run alembic.command through the repository
alembic.ini. Drop that database from a finally block. Query table/index/trigger
state through asyncpg. Prove:

    upgrade("0047_generation_telegram_reports")
    insert_one_observer_row()
    upgrade("head")
    assert not table_exists("generation_telegram_reports")

And:

    upgrade("head")
    downgrade("0047_generation_telegram_reports")
    assert table_exists("generation_telegram_reports")
    assert scalar("select count(*) from generation_telegram_reports") == 0
    assert has_index("ix_generation_telegram_reports_due_work")
    assert has_trigger("generation_telegram_reports_set_updated_at")

- [ ] **Step 2: Verify RED**

    uv run pytest -q tests/test_generation_telegram_removal_migration.py

Expected: FAIL because revision 0048 does not exist.

- [ ] **Step 3: Implement the linear migration**

    revision = "0048_remove_telegram_reports"
    down_revision = "0047_generation_telegram_reports"

    def upgrade() -> None:
        op.execute(
            "DROP TRIGGER generation_telegram_reports_set_updated_at "
            "ON generation_telegram_reports"
        )
        op.drop_index(
            "ix_generation_telegram_reports_due_work",
            table_name="generation_telegram_reports",
        )
        op.drop_table("generation_telegram_reports")

    def downgrade() -> None:
        op.create_table(
            "generation_telegram_reports",
            sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("start_state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
            sa.Column("start_message_id", sa.BigInteger(), nullable=True),
            sa.Column("finish_state", sa.Text(), server_default=sa.text("'waiting_terminal'"), nullable=False),
            sa.Column("terminal_status", sa.Text(), nullable=True),
            sa.Column("last_stage", sa.Text(), server_default=sa.text("'accepted'"), nullable=False),
            sa.Column("start_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("finish_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("start_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finish_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_delivery_error_code", sa.Text(), nullable=True),
            sa.Column("preview_error_code", sa.Text(), nullable=True),
            sa.Column("preview_deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint(
                "start_state IN ('pending', 'sending', 'sent', 'failed', 'suppressed')",
                name="ck_generation_telegram_reports_start_state",
            ),
            sa.CheckConstraint(
                "finish_state IN ('waiting_terminal', 'waiting_preview', 'pending', "
                "'sending', 'sent', 'warning_sent', 'failed', 'suppressed')",
                name="ck_generation_telegram_reports_finish_state",
            ),
            sa.CheckConstraint(
                "terminal_status IS NULL OR terminal_status IN ('completed', 'failed', 'cancelled')",
                name="ck_generation_telegram_reports_terminal_status",
            ),
            sa.CheckConstraint(
                "last_stage IN ('accepted', 'routing', 'director', 'writer', 'images', "
                "'acceptance', 'snapshot', 'preview')",
                name="ck_generation_telegram_reports_last_stage",
            ),
            sa.CheckConstraint("start_attempts >= 0", name="ck_generation_telegram_reports_start_attempts_nonnegative"),
            sa.CheckConstraint("finish_attempts >= 0", name="ck_generation_telegram_reports_finish_attempts_nonnegative"),
            sa.ForeignKeyConstraint(["run_id"], ["generation_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("run_id"),
        )
        op.create_index(
            "ix_generation_telegram_reports_due_work",
            "generation_telegram_reports",
            ["start_state", "finish_state", "start_next_attempt_at", "finish_next_attempt_at", "lease_until"],
        )
        op.execute(
            "CREATE TRIGGER generation_telegram_reports_set_updated_at "
            "BEFORE UPDATE ON generation_telegram_reports "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )

Copy the schema definition from 0047 rather than importing another migration.

- [ ] **Step 4: Verify GREEN and single-head safety**

    uv run pytest -q       tests/test_generation_telegram_removal_migration.py       tests/test_migrations_single_head.py

Expected: PASS.

- [ ] **Step 5: Add the migration test to local-release-gate and commit**

    git add -- apps/api/migrations/versions/0048_remove_generation_telegram_reports.py       apps/api/tests/test_generation_telegram_removal_migration.py       infra/release/local-release-gate.sh
    git commit -m "feat(db): remove Telegram observer state"

---

### Task 3: Remove the per-generation observer runtime

**Files:**
- Delete: apps/api/scripts/dev_generation_telegram_acceptance.py
- Delete: apps/api/src/omnia_api/models/generation_telegram_report.py
- Delete: apps/api/src/omnia_api/services/generation_telegram_delivery.py
- Delete: apps/api/src/omnia_api/services/generation_telegram_reports.py
- Delete: apps/api/src/omnia_api/workers/generation_reports.py
- Delete: apps/api/tests/test_dev_generation_telegram_acceptance.py
- Delete: apps/api/tests/test_generation_report_compose.py
- Delete: apps/api/tests/test_generation_report_worker.py
- Delete: apps/api/tests/test_generation_telegram_delivery.py
- Delete: apps/api/tests/test_generation_telegram_preview.py
- Delete: apps/api/tests/test_generation_telegram_reports.py
- Modify: apps/api/src/omnia_api/core/config.py
- Modify: apps/api/src/omnia_api/models/__init__.py
- Modify: apps/api/src/omnia_api/routers/messages.py
- Modify: apps/api/src/omnia_api/services/generation_runs.py
- Modify: apps/api/src/omnia_api/workers/preview.py
- Modify: apps/api/tests/test_generation_runs.py
- Keep: secret_safety.py and test_secret_safety.py
- Keep: render_settle.py and test_render_settle.py

**Interfaces:**
- Consumes: generation lifecycle behavior from parent revision 8ad25ffd.
- Produces: generation/preview paths with no observer persistence or external delivery.

- [ ] **Step 1: Delete observer-only modules and tests**

Delete only the listed observer files. Do not remove generic token redaction or Telegram app-generation tests.

- [ ] **Step 2: Remove observer settings and model registration**

Remove dev_generation_telegram_reports, telegram_bot_token, and telegram_chat_id from Settings. Remove GenerationTelegramReport from models/__init__.py.

- [ ] **Step 3: Remove lifecycle and preview hooks**

Review the exact 7f2c6b6e parent diff for messages.py, generation_runs.py, preview.py, and test_generation_runs.py. Remove observer imports, calls, and fail-soft wrappers while preserving unrelated hunks.

- [ ] **Step 4: Run focused regressions**

    uv run pytest -q       tests/test_telegram_reporting_removed.py       tests/test_generation_runs.py       tests/test_render_settle.py       tests/test_secret_safety.py

The module-absence assertion must pass. Production-surface assertions remain red until Task 4.

- [ ] **Step 5: Lint, typecheck, and commit**

    uv run ruff check .
    MYPYPATH=src uv run mypy src
    git add -- \
      apps/api/scripts/dev_generation_telegram_acceptance.py \
      apps/api/src/omnia_api/core/config.py \
      apps/api/src/omnia_api/models/__init__.py \
      apps/api/src/omnia_api/models/generation_telegram_report.py \
      apps/api/src/omnia_api/routers/messages.py \
      apps/api/src/omnia_api/services/generation_runs.py \
      apps/api/src/omnia_api/services/generation_telegram_delivery.py \
      apps/api/src/omnia_api/services/generation_telegram_reports.py \
      apps/api/src/omnia_api/workers/generation_reports.py \
      apps/api/src/omnia_api/workers/preview.py \
      apps/api/tests/test_dev_generation_telegram_acceptance.py \
      apps/api/tests/test_generation_report_compose.py \
      apps/api/tests/test_generation_report_worker.py \
      apps/api/tests/test_generation_runs.py \
      apps/api/tests/test_generation_telegram_delivery.py \
      apps/api/tests/test_generation_telegram_preview.py \
      apps/api/tests/test_generation_telegram_reports.py
    git commit -m "refactor(api): remove generation Telegram observer"

---

### Task 4: Remove daily canary delivery and Compose wiring

**Files:**
- Delete: infra/monitoring/telegram_generation_report.py
- Delete: apps/api/tests/test_telegram_generation_report.py
- Modify: .github/workflows/production-generation-canary.yml
- Modify: .github/workflows/ci.yml
- Modify: apps/llm-gateway/deploy/full/docker-compose.yml
- Modify: infra/.env.example
- Modify: infra/ci/README.md
- Modify: infra/release/test-compose-policy.sh
- Keep: production_generation_canary.py and test_production_canary.py

**Interfaces:**
- Consumes: only canary credentials and expected release SHA.
- Produces: unchanged build/edit, incident, and result-artifact behavior without Telegram.

- [ ] **Step 1: Remove Telegram validation and delivery from the workflow**

Remove TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and the final reporting step. Keep PRODUCTION_CANARY_RESULT_FILE.

- [ ] **Step 2: Delete the helper/test and stale CI entries**

Delete the helper and its test. Remove deleted observer test names from CI and the local gate. Keep test_production_canary.py.

- [ ] **Step 3: Delete Compose service and variables**

Remove generation-report-worker and DEV_GENERATION_TELEGRAM_REPORTS from API/general worker. No Compose service may receive either Telegram secret.

- [ ] **Step 4: Rewrite Compose policy as absence assertions**

    assert "generation-report-worker" not in services
    for service in services.values():
        environment = service.get("environment", {})
        assert "DEV_GENERATION_TELEGRAM_REPORTS" not in environment
        assert "TELEGRAM_BOT_TOKEN" not in environment
        assert "TELEGRAM_CHAT_ID" not in environment

- [ ] **Step 5: Verify GREEN and commit**

    bash infra/release/test-compose-policy.sh
    uv run pytest -q       tests/test_telegram_reporting_removed.py       tests/test_production_canary.py
    git add -- \
      .github/workflows/ci.yml \
      .github/workflows/production-generation-canary.yml \
      apps/api/tests/test_telegram_generation_report.py \
      apps/llm-gateway/deploy/full/docker-compose.yml \
      infra/.env.example infra/ci/README.md \
      infra/monitoring/telegram_generation_report.py \
      infra/release/test-compose-policy.sh
    git commit -m "ci: remove Telegram generation reports"

---

### Task 5: Add repeatable secret removal and rewrite release operations

**Files:**
- Create: infra/release/remove-env-value.sh
- Modify: infra/release/test-release-tools.sh
- Modify: infra/release/README.md
- Modify: infra/release/local-release-gate.sh
- Delete: docs/superpowers/plans/2026-08-21-dev-generation-telegram-observer.md
- Delete: docs/superpowers/specs/2026-08-21-dev-generation-telegram-observer-design.md

**Interfaces:**
- Produces: bash remove-env-value.sh ENV_FILE KEY, removing all exact key assignments atomically while preserving mode/owner and printing only key/path.
- Consumes: release/rollback SHAs, backup paths, rollback images, and migration 0048.

- [ ] **Step 1: Add failing remover tests**

Test duplicate-key removal, unrelated-line preservation, mode/owner preservation, redacted output, and rejection of unsafe keys, symlinks, and missing files. Run test-release-tools.sh and verify RED because the helper is absent.

- [ ] **Step 2: Implement the minimal helper**

Validate the key with ^[A-Z_][A-Z0-9_]*$, reject symlink/non-regular input, create a same-directory candidate, remove exact KEY= lines with awk, preserve ownership/mode, atomically replace, and print only:

    removed KEY from /path/to/env

- [ ] **Step 3: Rewrite the runbook**

Remove observer enablement, acceptance, report-worker rollout/rollback, and pre-public disable sections. Add one-time removal-release steps that:

1. prove observer disabled and report-worker absent;
2. persist 0048 outside checkout before database mutation;
3. preflight the 7f2c6b6e rollback image with the 0048 compatibility mount;
4. deploy normally;
5. remove all three keys through a protected env candidate;
6. recreate API/general worker and re-prove health;
7. prove no running container contains removed keys;
8. prove to_regclass("generation_telegram_reports") is null;
9. delete GitHub secrets by name and require manual BotFather revocation.

- [ ] **Step 4: Delete superseded observer docs and update contracts**

Keep this removal spec/plan. Make test-release-tools reject old observer instructions and require the new compatibility/removal procedure.

- [ ] **Step 5: Verify and commit**

    bash infra/release/test-release-tools.sh
    bash infra/release/test-compose-policy.sh
    git diff --check
    git add -- \
      infra/release/remove-env-value.sh \
      infra/release/test-release-tools.sh \
      infra/release/README.md \
      infra/release/local-release-gate.sh \
      docs/superpowers/plans/2026-08-21-dev-generation-telegram-observer.md \
      docs/superpowers/specs/2026-08-21-dev-generation-telegram-observer-design.md
    git commit -m "ops: remove Telegram reporting configuration"

---

### Task 6: Full verification and diff audit

**Files:**
- Modify only files required by concrete failures.

**Interfaces:**
- Produces: a clean release branch with no reporting surface and no unrelated regression.

- [ ] **Step 1: Audit identifiers**

Search generation-report-worker, DEV_GENERATION_TELEGRAM_REPORTS, telegram_generation_report, dev_generation_telegram_acceptance, and generation_telegram_reports across apps, .github, and infra. Allow only migrations 0047/0048 and the two removal tests. Audit TELEGRAM_BOT_TOKEN separately; allow only user-project templates, generic secret safety, and removal contracts.

- [ ] **Step 2: Run API release gate**

Run ruff, mypy, release-critical API tests, migration removal tests, and negative contract tests with the CI PostgreSQL/Redis environment.

- [ ] **Step 3: Run release, orchestrator, and web gates**

Run test-release-tools.sh, test-compose-policy.sh, the safety gate named by local-release-gate.sh, orchestrator pytest/mypy, and web typecheck/test/build.

- [ ] **Step 4: Review branch diff**

    git diff --check origin/main...HEAD
    git diff --stat origin/main...HEAD
    git status --short

Confirm every change maps to the approved spec.

- [ ] **Step 5: Commit only verification fixes if needed**

Stage exact named files and create a scoped fix commit. Never amend or force-push.

---

### Task 7: Publish, review, merge, and deploy

**Files:**
- No new repository files unless CI/review finds a scoped issue.
- Production artifacts: release record, encrypted backup, rollback pointer/images, and external 0048 compatibility shim.

**Interfaces:**
- Consumes: verified branch SHA, protected main, production access, and the release runbook.
- Produces: merged/deployed exact release SHA with no Telegram reporting surface.

- [ ] **Step 1: Push and open a ready PR**

    git push -u origin codex/remove-telegram-reporting

Open against main and call out observer-row deletion and verification evidence.

- [ ] **Step 2: Require green CI/review and merge**

Address concrete findings with new commits, rerun affected verification, and merge without rewriting history.

- [ ] **Step 3: Prepare exact production release**

Resolve RELEASE_SHA from protected main and use currently deployed 7f2c6b6e as ROLLBACK_SHA. Confirm zero active generations, create encrypted/full env backups, capture rollback images, and persist/preflight the 0048 compatibility shim.

- [ ] **Step 4: Deploy and prove exact health**

Build revision-tagged images, apply 0048, roll API/worker/web/orchestrator, and require local/public web/API/worker/orchestrator identities equal RELEASE_SHA. Report-worker remains absent.

- [ ] **Step 5: Run production smoke and paid build/edit canary**

Update PRODUCTION_EXPECTED_RELEASE_SHA and require both workflows to succeed at exact RELEASE_SHA.

- [ ] **Step 6: Purge active server and GitHub configuration**

Remove DEV_GENERATION_TELEGRAM_REPORTS, TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID from a protected full-env candidate, atomically install, recreate API/general worker, and re-prove health. Delete both Telegram secrets from GitHub production environment and repository scope if present. Never print values.

- [ ] **Step 7: Prove final production invariants**

Require exact release health, zero active generations, absent report-worker, absent observer table, absent removed keys in every running container, successful smoke, and successful paid canary. Preserve the general encrypted backup/rollback bundle for the normal acceptance window and remind the owner to revoke the token through BotFather.
