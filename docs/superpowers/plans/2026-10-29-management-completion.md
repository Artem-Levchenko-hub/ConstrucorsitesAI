# Universal MAX management completion

> **For agentic workers:** Execute with the existing Sol/xhigh owners; root owns integration, Git, migrations and production. Use test-driven-development and a final independent read-only review.

**Goal:** Every new app has working owner management from its first build; explicitly connect the published legacy QA app without losing business data.

**Architecture:** Creation persists catalog + leads for all new apps, with optional scenario orders/bookings. The existing canonical managed SDK, owner broker, assertions, CAS and immutable customer snapshots remain the authority. Legacy adoption is explicit: additive historical projection plus an insert-only compatibility mirror, then normal generation and publication.

**Tech stack:** Existing Python/FastAPI/Pydantic, Next.js/React/TypeScript and project PostgreSQL; no new production dependency.

**Spec:** [Owner console design](../specs/2026-10-29-owner-console-design.md), extended by the user's 29 October request to finish, verify and deploy the system for every new application. Implementation, testing, commit and production delivery are explicitly authorized.

## Constraints

- All agents GPT-6.1 Sol/xhigh; Astra forbidden. Preserve unrelated work and secrets.
- New defaults apply only at creation. Never silently opt in saved legacy configs.
- Catalog and leads mandatory; orders/bookings are scenario defaults that can be deselected.
- Administrative data changes create no generation or source publication.
- Legacy source rows, UUIDs, actors, dates, totals, item snapshots and pickup fields remain unchanged.
- No source workspace patching, forged receipts or bypassed source/runtime gates. Use normal generation and publication.
- No generic arbitrary-table owner adapter or security-definer migration.
- Exact pushed SHA, green CI, canonical production compose, all roles/health and real user-path proof required.

## Review focus

- Missing/empty wizard payload and older clients must still produce an initial management contract.
- Deleted options/categories must not leave stale selections or a false empty catalog.
- Legacy backfill and concurrent inserts must not lose or duplicate orders; conflicting UUID snapshots abort.
- Customer and owner scopes, owner notes and transaction-local GUCs must remain isolated.
- Lost responses, stale quotes and reloads must preserve operation identity and immutable historical prices.

## Tasks and ownership

### 1. Universal creation and explicit legacy activation — root

- [x] RED creation without settings/empty modules/scenario modules/explicit opt-out.
- [x] Persist management configuration in the project transaction, using one module-normalization helper.
- [x] RED owner activation payload/auth/CAS/preservation; implement an explicit owner endpoint using the existing config writer.
- [ ] Verify full affected API tests, strict types, lint and unchanged legacy defaults.

### 2. Wizard and catalog selection SDK — frontend Sol

- [x] RED React regressions for options removed/replaced and selected category renamed/removed after refresh.
- [x] Add backward-compatible useYleumCatalogSelection(items), deriving only currently valid state.
- [x] Require the managed selection hook in the generator instructions and bounded source admission.
- [x] Mandatory catalog/leads in wizard; explicit old-app activation with CAS, dirty-state protection and honest pending-publication state.

### 3. Pickup compatibility and historical projection — backend Sol + root DTO integration

- [x] Add optional typed pickup fulfillment to canonical orders; compute server time after receipt lookup, preserve replay.
- [x] Keep existing resource registry/hashes compatible; validate/project optional fulfillment without exposing private notes.
- [x] Add reviewed explicit SQL recipe: one transactional backfill + AFTER INSERT mirror, security invoker and qualified names.
- [x] Preserve original legacy rows and canonical owner metadata in the recipe; restore transaction-local scope on every exit. Live data verification remains below.
- [ ] Native/PostgreSQL regressions for race/replay/collision/rollback/isolation and pickup snapshots.

### 4. Integration and delivery — root, independent Sol reviewer

- [ ] Review the whole diff and focused cleanup; run required native/API/web/template checks.
- [ ] Independent defect-first review, fix material findings, commit and push upstream without force.
- [ ] Exact-SHA Linux CI including real PostgreSQL; canonical deployment and revision/health/smoke proof.
- [ ] Fresh hash-only baseline of every existing legacy row and publication/DB/PVC identity.
- [ ] Explicitly enable old QA management, normal compatible generation, source review and publication.
- [ ] New QA app first build already has catalog and managed forms/history; publish through the ordinary owner flow.
- [ ] MAX customer order/enquiry → owner registry/status → customer reload; catalog save/price/options/category/hide without AI or republish.
- [ ] Republish a compatible change; compare all old rows and create/read a new record; record unsupported/blocked cases honestly.

## Initial evidence

Local/main baseline 8575590e; API/web observed at 5a3d4e19. Legacy active application commit 2a8cbba4. Thirteen server orders exist; all eleven earlier control rows match their full original-column hashes. Old QA owner registry is explicitly unsupported until adoption. These are starting facts, not final acceptance.

## Verification checkpoint before delivery

Independent platform review: no remaining findings after fixing the accepted-head
lock/refresh race and preservation of the canonical applied migration for empty
legacy module choices. Final local web suite: 153 files / 1513 tests passed;
types and lint passed (one existing apple-icon warning). The production build
passed for the unchanged production web components. API strict Linux-target
types: 372 files passed; full Ruff passed. The broader local API run hit 16
database fixture setup errors because no disposable PostgreSQL server is running
locally; the final non-database selection passed 456 tests with those 16 cases
explicitly deselected. They and all four native SDK SQL tests must pass on real
PostgreSQL in exact-revision Linux CI. Final SDK unit gate: 36 passed / 4 native
PostgreSQL cases skipped locally. Template materialization: 12 passed.

Independent review also reproduced lost operation identity after reload, a false
second success after acknowledged-storage cleanup failed, and reuse of a restored
actor-A input under actor B. Shared SDK fixes and real React recovery regressions
cover these cases. Durable pending inputs stay scoped to verified actor/origin;
unresolved attempts survive authentication failures and account switches.
Acknowledged cleanup failures block a new submission rather than reporting an old
receipt as a new success. Restored keys retain actor provenance. The candidate's
12 real-SDK React tests passed; its source admission remains bounded at 2771/4000.

A fresh read-only baseline captured all 13 existing legacy orders and confirmed
the earlier 11 control rows unchanged and the explicit recipe compatible. No
production mutation, paid generation or deployment has been performed for this
change yet; live acceptance remains outstanding below.
