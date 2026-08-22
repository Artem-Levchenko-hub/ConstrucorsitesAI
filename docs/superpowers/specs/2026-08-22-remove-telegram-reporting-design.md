# Remove Telegram Reporting Design

## Status

Approved in chat on 2026-08-22. The owner requested complete removal of both
Telegram reporting mechanisms:

1. the GitHub Actions daily production-generation canary report; and
2. the per-generation development observer that posts prompts, outcomes, and
   preview screenshots.

## Goal

Remove all runtime, CI, database, configuration, operational, and secret
dependencies that exist only to send Omnia generation reports to Telegram,
without reverting unrelated release hardening or Telegram-bot project support.

## Scope

The removal includes:

- the generation report outbox model, services, delivery client, and worker;
- all API, generation lifecycle, and preview hooks that create or update report
  state;
- observer acceptance tooling and observer-specific tests;
- the dedicated Compose service and all observer environment variables;
- the GitHub Actions canary reporting step, its helper, and its tests;
- production and CI documentation for both reporting mechanisms;
- the active production and GitHub copies of `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID`;
- the observer-specific database table and its accumulated rows; and
- observer-specific rollback bundles after the replacement release has passed
  production acceptance.

The removal does not include:

- generation canaries themselves, their incident lifecycle, or their result
  artifact;
- Telegram bot templates that users may choose as a generated project stack;
- generic secret redaction for Telegram-shaped bot tokens, because generated
  user projects can still contain such credentials;
- unrelated correctness changes shipped in `7f2c6b6e`, including bounded font
  settling, hermetic gates, creator migrations, and release tooling; or
- Telegram messages already present in the team group. Existing messages must
  be deleted in Telegram if desired.

## Repository Design

### Application runtime

Delete the observer-only model, service, delivery, and worker modules. Remove
their imports and calls from generation creation, lifecycle transitions,
message routing, cancellation, and preview rendering. Remove the three observer
settings from `Settings`.

The resulting API, general worker, and web behavior must be identical whether
the old observer variables are absent or still present in an external legacy
environment file. Pydantic may ignore those stale variables during the rollout,
but the deployment removes them after the new runtime is healthy.

### Compose and configuration

Delete `generation-report-worker` from the production Compose project. Remove
`DEV_GENERATION_TELEGRAM_REPORTS` from API and general worker environments, and
remove `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from every Compose service.
Delete observer examples from `infra/.env.example`.

Add a repository contract test that renders/scans the production configuration
and fails if any reporting service, reporting variable, delivery module, or
workflow Telegram step is reintroduced. The contract explicitly allows the
generic Telegram token redaction regex and user-facing Telegram project
templates.

### Daily canary

Keep the paid build/edit canary, JSON result artifact, incident creation, and
incident recovery behavior. Remove only the Telegram configuration check,
Telegram delivery step, reporting helper, and helper tests. The canary must run
successfully with only its existing canary credentials and expected release
SHA.

### Database migration

Keep `0047_generation_telegram_reports.py` unchanged as immutable Alembic
history. Add `0048_remove_generation_telegram_reports.py` whose upgrade drops
the observer table, including its index, trigger, and rows. Its downgrade
recreates the empty `0047` schema so migration reversibility remains testable;
it does not recover deleted observer rows.

Migration tests must prove all of the following against PostgreSQL:

- `0047 -> 0048` removes `generation_telegram_reports`;
- a clean migration to head finishes without the table;
- `0048 -> 0047` recreates an empty table with its primary key, foreign key,
  constraints, due-work index, and updated-at trigger; and
- the migration graph has one head.

## Production Rollout

The observer is already disabled and its container is absent. The replacement
release uses the current production revision `7f2c6b6e` as its rollback target.

Before mutating the live database, the release must:

1. confirm zero active generations;
2. capture the normal encrypted full backup and permission-preserving
   environment backups;
3. capture revision-tagged current API and web rollback images; and
4. persist the new `0048` migration as an external compatibility shim for the
   `7f2c6b6e` rollback API image, then prove that image can read the post-0048
   database revision and start healthily with the shim mounted.

Deploy the new revision through the documented revision-tagged production
Compose path. Require exact local and public identities for web, API, worker,
and orchestrator. Run the production smoke and the paid disposable build/edit
canary; the canary must no longer require or send Telegram reports.

After health, smoke, and canary succeed:

1. remove the three observer variables from the active full-stack environment
   using a permission-preserving candidate file;
2. recreate API and general worker and prove they remain healthy;
3. delete `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from both the GitHub
   production environment and repository scope when present;
4. verify no running container contains any of the three removed variables;
5. verify the observer table and report-worker container are absent; and
6. preserve the encrypted general release backup until the normal release
   acceptance window closes.

The active bot token cannot be revoked through the Bot API. The owner must use
BotFather to revoke it. Removing GitHub and server copies prevents further use
by Omnia, while revocation invalidates copies in chat history and encrypted
backups.

## Rollback

Rollback must never stamp the database revision or drop unrelated data. If the
replacement release must roll back after `0048` is applied, use the persisted
`0048` compatibility shim when starting the `7f2c6b6e` API image. The observer
flag remains false and the report worker remains absent, so the old runtime
does not create or deliver reports. Preserve the shim, rollback pointer, images,
and environment backups until the replacement release is accepted.

## Verification

Repository verification requires:

- a red-green removal contract test;
- migration upgrade/downgrade tests;
- API lint and type checking;
- the release-critical API regression suite;
- release tool and Compose policy tests;
- orchestrator tests and web typecheck/test/build when touched by the release
  gate; and
- a clean diff scan showing no reporting identifiers outside immutable
  migration history, the generic redaction rule, and this historical design
  documentation.

Production verification requires exact revision health, successful production
smoke, successful paid build/edit canary, zero active generations, no report
worker, no observer table, and no observer variables in any running container.

## Security and Data Destruction

Dropping `generation_telegram_reports` permanently deletes its observer-only
rows. The rows contain delivery state and run references, not canonical
projects, prompts, snapshots, previews, generation history, or billing data.
Canonical generation data remains untouched.

Commands and logs must never print Telegram secrets. Secret deletion is done by
name, and verification checks only presence or absence. The encrypted release
backup is not surgically modified because it is also the recovery artifact for
canonical production data.
