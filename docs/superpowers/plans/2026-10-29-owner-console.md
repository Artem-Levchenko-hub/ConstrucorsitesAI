# Yleum owner console — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for bounded implementations and task reviews; root owns integration and all Git/deployment operations. Steps use checkbox syntax.

**Goal:** Every new MAX app gets its supported owner management in Yleum: catalog, orders/leads/bookings, settings, statistics; ordinary data edits require no AI or publication.

**Architecture:** Platform module contracts select immutable managed SDK business resources. The Yleum owner API verifies project ownership and brokers a narrowly signed request to the active published runtime; records stay in the app's managed maxBusinessActions database. Existing catalog/config and analytics are reused. Legacy custom tables are never auto-read, rewritten or migrated.

**Tech Stack:** FastAPI/Pydantic/SQLAlchemy, Next.js/React/TypeScript/pg, existing managed MAX SDK, existing publication/controller and production release driver.

**Spec:** docs/superpowers/specs/2026-10-29-owner-console-design.md (user approved full scope and 100 catalog positions).

## Global constraints

- Catalog remains at 100 positions for this release; size/error safeguards remain.
- Source and published data never get rewritten merely by opening management.
- Owner authority is project-scoped Yleum ownership, never global admin or a customer MAX ID.
- No bot tokens or owner assertions reach the owner's browser.
- No arbitrary SQL/table/resource names from owner input; whitelist fixed managed resource IDs.
- Real data comes only from the active published runtime; preview data stays separate.
- Preserve prior order snapshots. Catalog changes affect only newly confirmed orders.
- No AI calls, generation credits, source snapshots or publications for ordinary data operations.
- No payments/refunds, broadcasts, employees, MAX owner binding or arbitrary schema UI.
- Root keeps Git and deployment serial; workers do not commit, push or deploy.
- Preserve all 13 records in existing QA Sever; no automatic legacy adaptation.
- Implement technical details inline without repeat product approval; full approved product scope is binding.
- Initial setup uses a manual linked worktree because the app worktree tool rejects the nested repository, not because Git failed.

## Review focus

1. Ownership changes, forged project/record IDs, unregistered private action types: never expand data access.
2. Lost update response/retry after later catalog or record changes: return the committed receipt, no double effect.
3. Stale active publication/runtime or mismatched contract: report unavailable/incompatible, never quietly read the wrong database.
4. Free-text/changed prices and hidden items: no fabricated zero, no stale automatic checkout, old snapshots retained.
5. Legacy custom schemas and unavailable runtimes: honest unsupported/unavailable state, not an empty business list.

## Shared contracts (locked before parallel work)

ManagementModule: catalog | orders | leads | bookings. Catalog is required by orders/bookings. Settings and statistics are baseline shell capabilities, not optional data-resource privileges.

ResourceId: orders | leads | bookings. Fixed storage action types: order_created, lead_created, booking_created. Only canonical version-1 payloads for these resources are visible; other private maxBusinessActions must not be exposed.

ManagementContract: protocol=1, modules=unique validated list, resources=immutable registry definitions, contract_hash=SHA256 of canonical JSON excluding contract_hash. New SDK managed manifest is .omnia/management.json; app code cannot grant arbitrary resources by putting SQL in it.

OwnerStatus: new | in_progress | completed | cancelled. RU labels are resource-specific (bookings in_progress = Подтверждена). Allowed transitions: new -> in_progress/cancelled, in_progress -> completed/cancelled; same status is a no-op. Notes may change without a status change. No silent reopen of completed/cancelled.

OwnerRecord: id(UUID), resource, created_at(aware UTC), revision(64 lowercase hex), status, customer_name, contact_phone/email optional, comment optional, requested_for optional, service_title/service_price_label optional(readonly booking snapshot), items(list), total_kopecks optional, currency RUB optional, note(owner only). No raw MAX profile/initData or arbitrary payload.

OwnerRecordItem: catalog_id, title, quantity, unit_price_kopecks, option optional. Amounts are integer kopecks; price parser accepts explicit whole/2-decimal RUB values, including explicit numeric zero, rejects text/ambiguous formats without inventing a zero.

OwnerRecordUpdate: expected_revision, operation_key(UUID), status optional, note optional(max 2000 chars); at least one change. Empty note clears; absent note preserves. Server receipt lookup happens before stale-revision rejection, with canonical request digest conflict protection.

OwnerRecordsQuery: resource, limit(default25,max100), cursor optional, query(max120), status optional, created_from/to aware ISO timestamps optional. Paginated response: records, next_cursor. No total creation quota.

ManagementOverview: state(active/not_published/unsupported/unavailable), configured_modules, active_modules, resources, publication_id optional, contract_hash optional, catalog_limit=100. Unsupported legacy schemas are explicit.

Owner browser paths:
- GET /api/projects/{project_id}/max/management
- GET /api/projects/{project_id}/max/management/records?resource=...
- PATCH /api/projects/{project_id}/max/management/records/{record_id}

Runtime broker path: POST /api/omnia/owner, strict body op=describe/list/update plus typed operation fields. Owner server assertion header X-Yleum-Owner-Assertion; version1, project_id, owner_id, publication identity, contract_hash, method, exact path, body_sha256, iat, exp<=iat+60. Derive a distinct purpose-scoped HMAC key from the configured project bot token; never reuse a customer session or integration assertion as owner authority. Bind the configured active runtime publication using an existing environment identity if available; otherwise add a narrow controller-provisioned publication identity. Signed bytes are the exact UTF-8 request content. Constant-time signature checks; expired/wrong project/path/body/contract/publication fail closed.

New customer SDK:
- src/lib/omnia/business-client.ts: createManagedOrder, createManagedLead, createManagedBooking, getManagedRecords.
- Customer route /api/omnia/business/[resource]: GET own records, POST canonical creation with operationKey; verified MAX session is sole actor.
- Order creation reads mounted current catalog; client submits configVersion and viewed integer kopeck prices. Changed quote ->409 quote_changed with refreshed quote; no automatic second POST. A deliberate second confirmation creates once.
- Booking payload is a request with requested_for; no invented real-time availability/capacity engine. Owner confirms manually. Generated UI must call it a request until confirmed.
- Durable managed business writes forward server action analytics once with stable record ID; ordinary client telemetry does not become proof of a transaction.
- Customer responses omit owner note and owner audit identity.

## Task 1: Module registry, DTOs and canonical fixtures

Files: create apps/api/src/yleum_api/schemas/max_management.py; create services/max_management_contract.py; create tests/test_max_management_contract.py; create tests/fixtures/max_management_contract_v1.json.

Produces: enums/DTOs above; validate_management_modules; build_management_contract; parse_management_contract; get_resource_definition. Pure functions only in this task, no mutation of existing config routes yet.

- [ ] RED: unknown/duplicate modules, missing catalog dependency, unknown fields, negative/invalid revisions, timestamp awareness and update-without-change fail.
- [ ] RED: stable contract hashes independent of dictionary order; exact RU field/status definitions and canonical fixture.
- [ ] Implement strict Pydantic DTOs and immutable module registry; no arbitrary payload/SQL.
- [ ] GREEN: scoped pytest + Ruff/mypy where available.
- [ ] Fresh task review; root serial commit after verifying owned diff.

## Task 2: Managed runtime and business modules

Files: new template src/lib/omnia/management-{config,auth,store,resources}.ts, business-client.ts, business-server.ts; new template src/app/api/omnia/owner/route.ts and business/[resource]/route.ts; new drizzle/0003_owner_management.sql; tests/harnesses under apps/api/tests/fixtures and tests/test_max_management_sdk.py. Modify only necessary template bootstrapping/schema files, coordinated with root.

Consumes Task1 DTO/fixture and fixed RPC contract. Produces customer SDK and owner RPC. Runtime modules must be safe to compile in legacy projects; absent contract/managed schema -> unsupported rather than bootstrap writes or broken existing feature routes.

- [ ] RED auth: cross-project/body/path/expired/customer assertion, wrong publication/contract, oversized input; no DB work before authentication.
- [ ] RED persistence: user A/B isolation; whitelisted types only; owner-visible projection excludes private raw payload and notes from customer responses.
- [ ] RED order: catalog authority, 409 separate quote confirmation, hidden/text price rejection, exact historical snapshots.
- [ ] RED replay/concurrency: same key returns same record; changed body conflicts; owner stale revision returns409; lost response repeat changes once.
- [ ] Implement purpose-scoped assertion verification, fixed SQL queries and typed projections; use existing pg/config infrastructure with bounded calls.
- [ ] Owner status/note metadata and audit stay separate from customer snapshots, with transactional update/replay.
- [ ] Add versioned schema changes only for compatible managed tables; never create/alter arbitrary legacy customer tables at request time.
- [ ] GREEN: real TS/Node harness and disposable PostgreSQL integration tests. No paid LLM calls.
- [ ] Fresh task review; root serial commit.

## Task 3: Yleum owner API broker

Files: new routers/max_management.py; services/max_management.py; services/max_management_assertion.py; tests/test_max_management_api.py; tests/test_max_management_broker.py. Modify main.py router registration; orchestrator-client/publication identity wiring only where required and coordinate shared files with root.

Consumes Task1 contracts, Task2 RPC. Produces three owner browser endpoints and validated ManagementOverview.

- [ ] RED: foreign owner/record/project, missing account session, active-vs-draft snapshot selection, unpublished/unregistered schema, stale publication/contract.
- [ ] RED: destination is derived from trusted deployment only; redirects and caller-supplied hosts forbidden; bounded read/write timeouts.
- [ ] RED: exact cross-language signed fixture, private fields/token stripping, invalid upstream record rejection; no automatic ambiguous write retry.
- [ ] Implement owner check before runtime access, acquire bot credential on server only, exact signed RPC request and typed error mapping.
- [ ] Configured modules and active modules are distinct; catalog/settings/statistics remain available for legacy, business unsupported is honest.
- [ ] GREEN: scoped API tests + schema/route lint/type checks.
- [ ] Fresh task review; root serial commit.

## Task 4: Owner console frontend

Files: new components/max/MaxOwnerConsole.tsx, MaxOwnerRecords.tsx, MaxOwnerSettings.tsx, max-owner-console.css; new app/(app)/max/[id]/manage/page.tsx; new lib/api/max-management.ts and tests. Modify MaxProjectNav, MaxSectionShell, MaxOwnerCatalog for reusable embedded content, lib/api/types.ts, supported project entry links. Own only frontend files; creation-wizard wiring is Task5/root.

Consumes three Task3 endpoints and shared DTO shape. Produces Yleum management with relevant module sections and existing catalog/analytics reuse.

- [ ] RED: relevant sections only, catalog and settings without MAX-account binding, honest not_published/unsupported/unavailable states and no false empty list.
- [ ] RED: list/search/status/date/page filters, correct selected record, stale revision UI, retained draft after failure, stable retry operation key.
- [ ] RED: historical amount/item snapshots read only, safe RU rendering of customer text, owner-only note never shown in customer fixtures.
- [ ] Reuse catalog editor and analytics without double page shells; keep old URLs working.
- [ ] Settings MVP edits existing supported app name, summary, support email through versioned config; no new calendar engine or invented fields.
- [ ] Responsive list/cards, ordinary forms/actions, loading/empty/error states, no implementation jargon in product copy.
- [ ] GREEN: targeted Vitest, web typecheck/build.
- [ ] Fresh task review; root serial commit.

## Task 5: First-generation wiring and admission

Files: modify schemas/max_studio.py and max_project_kit.py, max_content_delivery.py, relevant max_studio config/create handlers, MaxProjectWizard/types/api payload mapping, generation contract/validation/finalization and managed-kit regression fixtures. New tests/test_max_management_generation.py and template/protocol tests.

Consumes Tasks1–4. Produces explicit new-project module choices, managed-kit version bump and behavioural acceptance.

- [ ] RED: new catalog/order/lead/booking selections produce matching managed contract before initial build; old JSON/configs stay compatible and do not auto-enable business data access.
- [ ] Private management configuration is excluded from public business-config projection. Catalog edits preserve module selection.
- [ ] Managed manifest/auth/business SDK cannot be rewritten by the model; include all necessary new files in managed kit, retirement/locked paths and archive sync.
- [ ] Add useYleumAppProfile (or equivalent narrow mounted-profile reader) so name/summary/support changes have an actual dynamic consumer in newly generated apps.
- [ ] Generator uses managed business modules instead of direct custom table paths for selected managed scenarios; required modules must be implemented by actual consumer code.
- [ ] Acceptance combines source/dataflow checks with actual runtime module probes; import-only/no-op claims are rejected. Existing budgets and terminal-paid-call guards remain.
- [ ] Tests prove catalog mutation reads new runtime metadata and managed record create/read/update/isolation; scoped ephemeral fixtures only.
- [ ] GREEN: all touched generation/kit/source-progress suites and legacy fixtures.
- [ ] Fresh integration review; root serial commit.

## Task 6: Combined regression and delivery

- [ ] Self-review spec/plan coverage; all five Review Focus classes have explicit tests above.
- [ ] Run combined affected API, Node/TS, web suites; Ruff/mypy/typecheck/build and diff sanity.
- [ ] Run disposable Linux/PostgreSQL checks in exact-revision CI. Never run destructive fixture DB tests against production.
- [ ] Fresh whole-branch spec/security/code review. Fix material findings with RED->GREEN regression checks.
- [ ] Root integrates/pushes approved work to GitHub, using current upstream without force-push; preserve concurrent changes and resolve conflicts normally.
- [ ] Canonical production deployment only; confirm exact revision across API/web/workers/controller and health.
- [ ] New scoped QA app: first build already has management; publish to available test MAX bot without disturbing active user work; add service in Yleum, create a real test request, owner list/status, fresh reopen, no AI for data edits, preserved data after compatible app update.
- [ ] Preserve source/run/release/record IDs and screenshots in human-readable Russian HTML report; do not call unsupported/live-unverified cases passed.
- [ ] Existing QA Sever 13 records stay unchanged throughout.
- [ ] Final response states revision, tests, deployed health, user-visible behaviour and concrete limitations.

## Execution bookkeeping

Use this plan's ignored .superpowers/sdd/ workspace for progress, briefs and review artifacts. Root maintains a durable ledger and serial Git. After Task1 contracts are reviewed, runtime, broker and frontend may be implemented as independent bounded work with exclusive file ownership; integrate only at the declared interfaces and review each result before committing. Root owns Task5 shared-file wiring and delivery. No unrelated cleanup or paid model retry loops.
