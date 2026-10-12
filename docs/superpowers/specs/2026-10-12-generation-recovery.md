# Generation recovery and truthful completion

The owner requests sequential implementation of the failures documented in the
12 October audit, delivery to GitHub and production, and real generation checks.

## Contract

- Guards direct repair; a progressing segment continues in the same run and cell.
- Only actual source changes authorize continuation; reads, failed writes, and
  identical writes do not. Provider ambiguity, cancellation, and infra failure
  never authorize blind replay. Retain configured overall generation deadlines.
- Preserve an unfinished source checkpoint privately before restoring the accepted
  version. An explicit continuation may reuse only the adjacent failed run owned
  by the same user/project and based on the same accepted source revision.
- A stale green build cannot certify later changes. Completion requires fresh
  checks and the existing full MAX finalization/behavior/ownership gates.
- No snapshot/quota settlement/publication represents unfinished work as success.
- Use actual available components, profile fields, and schema; remove guard
  instructions that invent a CrudResource component.
- Distinguish provider refusals without exposing keys or raw provider responses.
- Existing business data, owners, bot bindings, and applied migrations remain safe.
- Acceptance includes real source change, full build/runtime, requested behavior,
  and persistence across new accepted versions. A completed flag is insufficient.

## Scope

Use existing deterministic repair/finalization rather than weakening it. Include
the already merged PR93 fixes in delivery. Test the concrete compiler failures
and generic recovery policy; do not change every customer's historical source
without an authorized generation. Do not promise success during an external
provider outage: show its actual blocking reason and preserve the candidate.
