# 11. Unleash-the-model architecture — thin primitives + agent loop + verification gates

> **Status:** design + initial implementation landed 2026-06-25. Single source of
> truth for the pivot from "templates that cage the model" → "model writes freely,
> gates guarantee the result." Owner directive: *use the full power of the model —
> it knows how to write realtime; don't box it in a template.*

## The problem (from the gap analysis)

Base44/Lovable/Bolt produce **prototypes, not real apps** — and the reason is NOT
a weak model. The model knows how to write realtime, auth, a backend. The reason is:

1. **Inconsistency** — write it from scratch every time and it's ~90% right; the
   10% is exactly where the security holes and "it's a toy" live.
2. **Unverifiability** — every app is different, so you can't *prove* it doesn't leak.
3. **Wasted effort** — re-deriving login/DB/ACL each time instead of the product.

**Freedom without verification = prototype.** That's the law. So the fix is not to
constrain *how* the model writes — it's to verify *what* it produced.

## The design — remove the cage at the INPUT, verify at the OUTPUT

Three layers. The model writes the app freely (like Claude Code); a thin set of
safe primitives is available to use or ignore; hard gates prove the result is real
and doesn't leak before it ships.

```
  (A) thin safe PRIMITIVES        — libraries the model MAY use or ignore
            +                        (realtime hub, members ACL, auth) — like
                                     Socket.io/NextAuth, NOT a cage
  (B) autonomous AGENT LOOP       — plan→write→run→read-errors→fix, writes the
            +                        whole app; stack-aware (right primitives per stack)
  (C) verification GATES          — prove "works + no leak" on the OUTPUT, regardless
                                     of HOW it was written; the loop self-heals
                                     against them until green
```

### The key decision: a security SPECTRUM, not one mode

The engine's safety came from "the model never writes the query." If we let it write
raw backend, we lose that *structural* guarantee. A behavioural gate can't prove the
*absence* of all leaks (it only tests the paths it checks). So:

- **Default = safe-by-construction.** Data access goes through the primitives
  (SDK/engine), which enforce auth + ownership + membership. The **backend guardrail**
  (static scan) forbids the one escape — importing `@/lib/db`/`drizzle`/`pg` in
  writer code. 95% of apps stay structurally safe.
- **Escape hatch = safe-by-verification.** When the model genuinely needs raw backend
  (a custom realtime protocol), it may — but then the **security gates become
  mandatory-blocking** for that project, plus adversarial leak-checks.

Most builders pick ONE (all-freedom → leaky, or all-cage → prototype CRUD). The
spectrum gives both: structural safety by default, verified safety on escape.

## What is built (all behind `use_agentic_builder`, off in prod)

| Layer | Where | Commit |
|---|---|---|
| **A** primitives usable per-stack | `agent_builder.LOOP_PROTOCOL` + `build_system_prompt` + `load_stack_system_prompt`; `messages.py` per-stack prompt; `realtime` in `CONTAINER_NEXT` | `04ebb30` |
| **A** realtime primitive | `templates/nextjs-realtime/src/lib/realtime/*` (SSE+Redis hub, rooms, presence) — validated idiomatic vs Next.js 15 | `28f1ee0` |
| **A** membership ACL primitive | entity engine `access:"members"` (relation-based row security) | `30e0475` |
| **B** unleash the loop | `agent_builder.SYSTEM_PROMPT` ban lifted — author custom server logic via SDK/engine | `4eacf2f` |
| **B/C** backend guardrail | `backend_guardrail.py` — static scan, raw-DB escape forbidden | `e5a6262` |
| **C** self-heal | `agent_gate_feedback.py` — gate verdicts → next instruction; `messages.py` runs the guardrail after `done`, feeds violations back (bounded) when `USE_AGENT_GATE_FEEDBACK` | `4eacf2f` |
| **C** functional gate | `functional_gate.py` — two members exchange a message live <1s; non-member 403 | `d66e393` |
| **C** security gate | `security_gate.py` — leak attempts + headers/CORS/payload | `b90ba8b` |
| **C** role gate | `role_gate.py` — multi-role enforcement matrix | `c8a297d` |
| **C** durability | migration single-head guard + regression registry | `5b16c5f`,`915fb87` |
| **C** API hardening | `fastapi-postgres` CORS/body-cap/headers/fail-fast secrets | `a8f7b7c` |

~50 unit assertions run green; templates TS-reviewed BUILD-CLEAN; the membership
ACL passed an independent adversarial review.

## How a build runs (target end-state)

```
user prompt
   ▼
agent loop (stack-aware prompt + primitives offered)
   ├─ plan → write_file/edit_file → build → read errors → fix … → done
   ▼
backend guardrail (static)  — raw-DB escape? → feed back, loop fixes
   ▼   (USE_AGENT_GATE_FEEDBACK)
functional + security + role gates (live preview, USE_*_GATE)
   ├─ red? → concrete failure fed back → loop fixes → re-run
   ▼
regression registry — anything that passed before now broken? → block
   ▼
ship (only when all blocking gates green)
```

The model wrote it however it wanted; the **gates**, not a template, decided it was
a real app.

## Integration tail (deploy/wiring, not new design)

1. Wire functional/security/role verdicts into the loop's self-heal list (today only
   the static guardrail is wired; the live gates plug into the same `GateOutcome`
   list with a preview URL).
2. Flip the live gates into the ship boolean in `acceptance.py` (not modified blind —
   needs a live run to validate, else risk false-blocking all projects).
3. Prod: rebuild template images, `alembic upgrade head` (0021), discovery
   auto-routing for the `realtime` stack, `apps/web` label sync, flip flags on.

## Flags (all default OFF — prod generation byte-unchanged)

`USE_AGENTIC_BUILDER` (the loop), `USE_AGENT_GATE_FEEDBACK` (guardrail self-heal),
`USE_RUNTIME_GATES` (functional gate in the loop), `USE_FUNCTIONAL_GATE`,
`USE_SECURITY_GATE`, `USE_ROLE_GATE`, `USE_BACKEND_GUARDRAIL`.

## Research findings (deep-research, folded in 2026-06-25)

A 4-angle cited deep-research pass (agent-loop reliability, LLM-backend security,
realtime-at-scale; the AI-builder-landscape angle dropped on a connection error)
**audited the code itself** and sharpened the plan. Sources incl. Reflexion
(arXiv 2303.11366), SWE-agent (2405.15793), TDFlow (2510.23761), Veracode 2025
GenAI Code Security, Cedar (2407.01688), BACFuzz (2507.15984), Postgres RLS docs.

**Validated by the evidence** (no change needed): the agent loop is a real harness
(anti-stall guards, recoverable-vs-terminal classification, structured execution
feedback — the Reflexion/SWE-agent pattern that drove HumanEval 80→91%); the
realtime ACL gates every subscribe/publish fail-closed; SSE-as-default is the
correct server-push choice; the backend-guardrail self-heal is correctly wired.

**The #1 gap it found — now FIXED:** `run_functional_gate`/`run_role_gate` were
defined + unit-tested but had **zero call sites** — the "gates prove no leak" spine
was never connected. The functional gate is now wired into the self-heal loop
(commit `d084707`, `USE_RUNTIME_GATES`).

**Honest reframe (adopted):** you cannot *prove* no-leak end-to-end — SAST/regex
(the backend guardrail) structurally **cannot** catch IDOR/BOLA, the dominant
AI-code flaw (Veracode: ~45% of AI code is insecure; broken access control is
OWASP A01). The correct framing is **"prove the primitive, fuzz the output"**: the
small auditable ACL is provable; the generated surface is verified by runtime
cross-tenant probes. So the negative-path gate must be *mandatory-blocking*, not
advisory.

**Remaining top actions (ranked), open as the work tail:**

1. **Generalize the cross-tenant probe to the model's OWN authored routes.** The
   functional gate today probes the FIXED messenger flow only; "unleash + verify the
   output" is unproven for generated surface until every generated `/<resource>/:id`
   gets an auto outsider-403 assertion (BACFuzz-style oracle — BAC failures are
   silent, so only this detects them).
2. **Postgres RLS deny-by-default backstop** in every tenant-scoped template table
   (`tenant_id NOT NULL`+FK, `ENABLE`+`FORCE ROW LEVEL SECURITY`, `USING`+`WITH
   CHECK` on `current_setting('app.current_tenant')`, non-owner/non-superuser app
   role, `SET LOCAL` per request) + a CI lint failing the build on any table missing
   it. Today isolation rests entirely on app-code `WHERE` clauses — one bug = silent
   cross-tenant leak with **no DB backstop**. This is the single highest-value
   security addition.
3. **Wire the role gate** (entities) into the same loop (needs a per-app role matrix)
   and make the **done-predicate gate-green**, not typecheck+5xx-smoke.
4. **Namespace the Redis bus per project** — `hub.ts` publishes all events to one
   global `omnia:realtime` channel; under prod replicas every project rebroadcasts to
   every other. Namespace `omnia:realtime:{project_id}` + authorize on subscribe.
5. **Per-connection SSE backpressure** — the stream enqueues unbounded; one slow
   tenant can OOM a shared gateway (multi-tenant blast radius). Add a bounded buffer
   with pause/drop.
6. **Redis Streams** (not just pub/sub) for durable/ordered events that must replay —
   the in-memory ring (`RING_SIZE=100`) is per-process and lost on hibernate.

**Standing risk:** all gates default **OFF** (`use_*_gate=False`), so the
"verification proves no leak" promise is **inert in prod today** — the architecture
exists; enforcement is switched off pending a live validation run, then flip the
flags. A messenger/CRM generated right now is verified by typecheck + a 5xx probe
only. Turning the flags on (after one live gate run confirms no false-blocking) is
the gate-to-real-product step.
