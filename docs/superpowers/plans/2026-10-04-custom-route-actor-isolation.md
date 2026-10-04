# Custom-route actor isolation implementation plan

> **For agentic workers:** Execute inline; root owns integration and deployment.

**Goal:** Supply a bounded, read-only acceptance runner for a disposable custom route with two genuine signed MAX actors, foreign project/owner credentials and anonymous requests.

**Architecture:** A standalone Node runner sends GET requests only to a loopback disposable app. It validates `/api/max/session` identity before checking collection and item bodies against synthetic fixtures. Tests exercise the real template HMAC/session source with a disposable HTTP fixture and deliberately broken row scoping. Local fixture success is harness/authentication evidence, never deployed custom-route or PostgreSQL evidence.

**Constraints:** Do not change managed kit/SDK sources, customer tables, production data or promotion policy. No provider calls, generation, push or deploy. Never print credentials, fixture values or raw response bodies. Denials must have an explicit denial status and no protected values; collections must contain their own fixture and exclude the other actor's fixture.

**Files:** `tools/qa/custom-route-actor-isolation.mjs` (CLI and reusable probe), `tools/qa/custom-route-actor-isolation.test.mjs` (real authentication source and HTTP safety tests), and a short evidence/readme file.

- [x] Write tests for actor identity, own list/item read, foreign list/item denial, anonymous/foreign credential denial, leaking denial bodies, loopback-only targets and GET-only traffic.
- [x] Run and observe the missing runner failure.
- [x] Implement the bounded runner; no POST login or DB mutation.
- [x] Run all new tests and existing relevant MAX auth/template tests; check the diff.
- [x] Commit locally and return exact evidence plus deployed/DB gaps to root (delivery remains root-owned).
