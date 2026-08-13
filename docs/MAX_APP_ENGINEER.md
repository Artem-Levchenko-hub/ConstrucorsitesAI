# Omnia MAX App Engineer

## Purpose

The MAX App Engineer is the specialised product identity of Omnia's existing
native code agent. It does not fork a second generator and is not a larger prompt
or a bundle of design plugins. It combines the full native coding loop, safe
project tools, a maintained MAX runtime and executable release gates in one
observable loop. MAX is the platform boundary, not a product/design template.

## Lifecycle

1. The shared discovery and BuildPlan establish screens, data and user outcomes.
2. The native prompt states the platform identity explicitly: build inside the
   MAX messenger, never an ordinary site or Telegram/VK Mini App.
3. The agent chooses product structure and art direction itself. There is no
   deterministic DesignDNA/template and no mandatory skill/planning ceremony.
4. The full safe tool surface is available: source reads/writes, docs, optional
   skills/MCP evidence, build, logs, runtime, signed visual inspection, media and
   a real shell inside the current project's isolated `/app` container.
5. Shell deltas are discovered from the live source tree, checkpointed and passed
   through the same MAX path, secret and SAST policy as normal file writes. Failed
   or forbidden mutations are rolled back.
6. The agent compiles, runs and inspects the signed preview. `broken`, `generic`,
   a score below the visual floor or missing factual proof is red and repaired.
7. The final release proof re-runs build, runtime, hydration, signed navigation,
   primary/persisted actions and reload restoration. Functional and 360/390
   visual evidence enter the same snapshot transaction.

Studio receives every action through the existing `agent.step` event shape, so
no parallel progress protocol or large UI surface is required.

## Why quality becomes reliable

Plugins can add references, imagery or telemetry, but cannot prove that an action
works. Reliability comes from the continuous tool loop plus independent facts:

- the server-owned MAX runtime keeps Bridge, signed identity, bot and managed
  integrations stable while the agent freely owns the product;
- optional skills/docs/MCP evidence can be loaded when useful but never block code;
- build/runtime feedback drives repair instead of ceremonial retries;
- independent screenshots and functional gates reject generic polish, fake data,
  fake success and missing evidence before release.

The model owns visual authorship. The platform owns isolation, truth boundaries
and the acceptance floor.

## Durable continuity contract

An accepted MAX build is a durable server job, not a coroutine owned by the
browser or one API process. Postgres keeps the public run, immutable execution
envelope, plan/evidence checkpoint and a renewable single-flight lease. RQ owns
execution. Durable generation has dedicated worker capacity and cannot sit
behind preview/Playwright work; preview has a 150-second cooperative pipeline
deadline plus an independent 180-second RQ horse limit. The worker supervisor
reaps children on shutdown, kills an uncooperative child after the grace period
and restarts a child that exits unexpectedly. Browser reload reconnects to the
same run and message.

Every queue attempt first reserves an enqueue generation atomically in the
run's Postgres continuity state. The token is part of the stable RQ job ID and
must match when the worker claims the lease. Repeated watchdog ticks therefore
enqueue once; an old or pre-deploy backlog job exits without touching the
provider, files, usage or snapshot. A lost Redis enqueue acknowledgement clears
only its exact reservation, while delayed retries keep their reservation across
the complete backoff window. After a worker death the expired lease is reclaimed
as the same run/message/checkpoint with a new enqueue generation.

Redis holds the opaque native transcript and provider turn cursor for 48 hours.
The agent checkpoints immediately before a provider request and after every
assistant/tool-result turn. If the network, API or worker dies with settlement
unknown, recovery sends the identical transcript and logical turn ID so the
gateway can replay its settled result without a duplicate provider request.
Hidden model reasoning never enters the public `GenerationRun.agent_state`.

Every provider call also receives a compact server-owned working note. It is
derived only from executed tools: current phase, changed artifacts, product-entry
state, latest build/proof facts, repeated observations and the next required
action. The note is ephemeral in the provider transcript, while its counters and
file revisions live in the Redis checkpoint; it therefore survives a worker/time
slice without accumulating another copy on every turn. The public plan remains a
user-visible notebook, not a ceremonial completion gate.

The production API and generation worker both run with `MOCK_LLM=false`.
Visual QA executes inside the worker; omitting that setting makes its local-safe
default disable the real judge and turns every successful screenshot into a
`visual_proof_unavailable` continuation loop.

MAX always enters the native project-agent path even if an old generic
`USE_AGENTIC_BUILDER` or `USE_NATIVE_AGENT` rollout flag is absent. The MAX
contract therefore cannot silently fall back to the legacy one-shot writer.
The agent has the full project-scoped capability surface: file search/read/write,
an audited shell, dependency installation, tests, typecheck, live logs/runtime,
signed browser vision, media generation, maintained skills and approved read-only
MCP research. Shell mutations are diffed across the complete source tree and
rolled back if they touch managed files or violate path, secret, SAST or DB rules.
Host Docker, other projects and environment secrets remain outside the capability
boundary; they do not help product engineering and would break tenant isolation.

Durable continuation is progress-aware. Every stopped segment records a digest of
the actual generated tree. A transient failure can resume, and a segment that
changes source may keep working, but the third recurrence of the same stop against
the same bytes is terminal. Exhausted visual repairs and a red final signed proof
are terminal immediately because replaying the same checkpoint cannot create new
evidence. This prevents both straight and alternating checkpoint cycles without
weakening the build/runtime/visual release proof.

Fresh builds allow one bounded inspection turn, then require the real
`ProductApp.tsx` vertical slice and compile it before more exploration. Existing
products keep unrestricted surgical support-file edits. Reading the same unchanged
file or repeating the same read-only shell/search observation is rejected until a
source mutation changes its revision. A focused product-entry checkpoint replaces
stale exploratory history, so continuation resumes from authoritative live files
instead of replaying hundreds of old reads.

Each execution slice may end. Internal compile, dependency, import, managed-API,
runtime, persistence, design or proof debt continues from the same live files and
plan while source or proof evidence is progressing. A repeated identical stop
against identical bytes, exhausted visual repair or red final signed proof ends
the run honestly and restores the last working version instead of replaying the
checkpoint. Permanent provider rejection and sustained external outage keep their
separate external classifications and exact owner action.

Partial files remain a private runtime checkpoint. No snapshot is published and
no completion is recorded until the full functional, signed visual and release
contract returns `contract_green`; the final snapshot transaction remains the
single publication point.

`GenerationContinuationRequired` is control flow, not an application failure.
It is re-raised through the prompt processor to the durable wrapper, which
schedules the next slice without calling the snapshot finalizer or deleting the
native checkpoint. This specifically covers provider conflicts/timeouts that
produce no AI write: they remain pending with classification and backoff instead
of becoming `build finished without a committed snapshot`.
