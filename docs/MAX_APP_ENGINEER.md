# Omnia MAX App Engineer

## Purpose

MAX App Engineer is one native code agent inside an Omnia-owned state machine.
MAX supplies the locked Bridge, signed identity, managed integrations, legal
routes and secret boundary. The model owns only the visible product source.
The target is a complete application, never a mockup, decorative landing page
or a component that merely compiles.

## Canonical fresh-build lifecycle

1. MAX Studio turns the completed questionnaire into a strict, bounded
   `MaxProductSpec`: purpose, audience, screens, capabilities, primary action plus its
   closed execution kind, data/history, integrations, one style direction and acceptance
   criteria. A fresh MAX build
   without this contract is rejected; it cannot fall back to the legacy loop.
2. Omnia deterministically derives a non-empty `BuildPlan` with screen, data and
   scenario identifiers and persists it before any paid provider turn.
3. The skill router preloads the smallest relevant set: UI/UX, MAX platform, one
   domain/product pack and only the persistence, AI, payment or safety packs the
   ProductSpec actually needs. The model does not choose or load skills itself.
4. The model receives one task: ProductSpec, plan, selected compact guidance and
   exact managed API signatures. It exposes only `write_files`; Omnia itself
   finishes after the objective checks are green.
5. The initial `write_files` call is one validated multi-file revision. It must
   include `ProductApp.tsx`, `globals.css` and at least one separate product
   component, hook or data module. Omnia validates every path/content first and
   performs one atomic hot reload only if the entire batch is safe.
6. Omnia automatically runs enriched build, live runtime and the signed objective
   functional gate. These checks consume no model turn.
7. If the revision is red, the model receives the complete bounded compiler,
   source-contract, plan and functional evidence plus the current source from its
   own revision. It gets one coherent `write_files` repair. Omnia then reruns all
   checks automatically.
8. A snapshot is created only when every same-revision proof is green. Otherwise
   the run ends honestly and the exact pre-run/last-known-good tree is restored.

## Objective completion contract

The signed browser gate proves behaviour, not visual taste:

- the signed MAX application opens and hydrates without browser or managed-request
  errors;
- every planned screen is actually reachable through a visible semantic control;
- the visible screen after navigation has the expected plan identifier, so dead
  source markers cannot satisfy completion;
- every planned action has exactly one reachable, enabled, semantic control whose
  accessible name and contract label match the plan; secondary actions are not
  auto-clicked because a generic harness cannot prove arbitrary business meaning;
- the planned primary action is exercised as a user after real form/selection
  controls are prepared; its closed execution kind is mutually exclusive:
  `local_navigation` requires an exact planned-screen transition, `managed_write`
  requires a causal managed response with a server-generated record id and restored
  history, and `catalog_read` requires a causal catalog response whose real item
  value is shown in the scoped outcome. A shared generic status/local toggle is not
  evidence;
- when history is required, a successful managed write is followed by a successful
  post-reload managed read containing the exact id and a visible restored item with
  the same `data-omnia-record-id`;
- a managed primary flow is replayed with its observed request forced to fail;
  the app must show a new scoped alert/error and must not add a success marker;
- navigation, controls, headings, touch targets, overflow and labels satisfy the
  deterministic mobile/accessibility checks;
- loading, empty, error/retry and success behaviour is implemented without fake
  records, swallowed failures or simulated success.

Subjective screenshot/design scoring is forcibly disabled for ProductSpec kernel
runs even if an old environment flag is enabled. The UI/UX pack sets one style
direction before code; no screenshot-polish or redesign loop can block a working
application.

## Loop and interruption guarantees

- The explicit kernel provider limit is four turns, not the historical 120-turn
  MAX limit. Normally only the initial source pass and one repair use turns; the
  spare turns absorb malformed/prose responses without opening another repair.
- A provider response may contain at most one atomic source revision. The entire
  run permits at most two successful source passes: initial plus repair.
- Build/runtime/functional checks are kernel transitions. The model cannot spend
  turns repeatedly asking for them, and all legacy write-floor/gate/coverage heal
  agents are disabled for the strict run.
- Every provider response and individual side effect has a durable checkpoint.
  Resume reconciles exact intended file bytes and repeats only read-only proof;
  it never repeats an uncertain source mutation.
- The exact pre-run product tree is stored once for the whole generation run and
  reused by every continuation slice; later red files can never become the rollback baseline.
- Temporary provider/orchestrator failure resumes the same run and checkpoint with
  bounded backoff. Internal continuations stop after three attempts or 30 minutes;
  persistent external provider outage has its separate 24-hour ceiling.
- One failed repair, a repeated semantic blocker or a red final proof is terminal.
  No automatic continuation re-enters the model against identical source.
- A missing owner-controlled provider or safe test-mode is terminal external debt:
  Omnia names it without spending the single code repair or retrying proof as infrastructure.
- Partial files remain private runtime state. Studio shows generation in progress
  until the final snapshot transaction commits; a partial app is never presented
  as complete.

## Source, security and code-intelligence boundary

Generated source cannot edit the locked runtime, package/build configuration,
server routes, secrets or direct database modules. Model-owned paths pass secret,
SAST, MAX path and direct-DB checks before the atomic write. User history and
identity use the managed, tenant-scoped MAX actions/profile APIs.

Code intelligence enriches the existing automatic build; it is not another tool
or agent. TypeScript, pinned Oxlint/dependency analysis and structured diagnostics
return errors first with affected paths and stable signatures. The final release
proof separately requires a completed OSV scan and an empty dedicated security
finding list, so advisory diagnostics cannot hide a vulnerability.

## Publication identity

Readiness is revision-exact. `published=true` requires a completed deployment whose
reported `commit_sha` equals the current snapshot and a passing deploy proof bound
to the same SHA. Timestamps or a production URL alone never prove that the current
application is live.

The snapshot/message/attestation transaction is the single completion point. After
it commits, deployment, push and health evidence can refer to one immutable revision;
before it commits, the product remains unfinished.
