# Requested collection controls: bounded source acceptance guard

## Confirmed QA failures

The two real parallel QA rounds each completed both pipelines but accepted only
one of the two requested UI changes. These are separate cohorts, not a stability
streak or proof of the complete MVP.

- R1 Fitness `222be365-410c-41b3-8112-7fe0e3507c4e`, accepted
  `973c08cfe8522b4efaa4d9f0b0d7b6450486a288`: the helper replaced an absent
  JSX anchor and printed success; `setFilter` was unused and the three requested
  controls/filtered-empty/reset UI were absent. Helper strings were not UI.
- R2 Coffee `c0a8dd60-e62f-4a2d-ba2d-348bdb98d456`, accepted
  `1207282ed6ffd5e83048dabbad0110abb00c76ac`: `setSortOrder` was unused,
  `sortedCatalog` was disconnected, the page still mapped `filteredCatalog`.
  All three requested sort controls were absent in the real preview. An adjacent
  blank leads page was also introduced during repair; this guard does not check
  the requested file scope.
- R2 Fitness `06abae4d-9f3c-4f52-a12c-5772b35af479`, accepted
  `cdef866e4fd2bea305f4c3f3add9db3ae6a858d6`: real filter/boundary/empty/reset,
  reload, keyboard and 390px checks passed. Its existing static history view is
  a separate unresolved UI defect, not evidence of lost database history.

R2 ended with no active generation from that pair, 2/2 pipeline completion and
1/2 functional acceptance. Exact overlap was 540.178192 seconds. The published
MAX/amoCRM moderation demo was preserved; these drafts were not published.

## Change and acceptance boundary

`max_source_completion_gap` now adds a negative TSX witness for explicit visible
filter/sort requests before portable source-word checks. It parses source with
locked tree-sitter dependencies without executing an application or helper.
For a supported default-function root, a known React string-state collection
operation with an unreferenced setter returns an actionable source gap. A fresh
array sort with a pure expression comparator and no referenced derived result
also returns a gap. The existing finalizer returns `NEEDS_EDIT` before full
build/candidate creation, then retains all normal build/runtime/release proofs.

This is **not** proof that controls are visible, clickable, correct or complete.
`None` means no supported negative witness, never semantic PASS. Unsupported
roots, aliases/references, alternative state paths, collection delegation to a
child, named control components, callback shadowing, in-place sorting and
uncertain syntax remain inconclusive. The guard does not prove labels, all three
controls, empty/reset behavior, history rendering, data preservation, or scope.

The regressions are reduced forensic reproductions, not copies of the complete
accepted Windows applications. Applying the installed guard to the exact real
accepted source and original prompts remains a release-acceptance follow-up for
the UI/deployment owner. Do not claim those exact source replays already passed.

## Verification and delivery

- RED tests reproduce the zero-match helper, missing setter and disconnected sort
  result; GREEN fixes retain counterexamples for valid alternative implementations.
- PostgreSQL finalizer tests assert no build and no candidate for each negative
  witness; a repaired filter resumes the unchanged guarded build/runtime path.
- Local verification: 203 related tests passed against disposable PostgreSQL 16
  (including the 41 source-contract cases), Ruff passed and mypy passed for 280
  API source files. These overlapping counts are not added together.
- Final independent review: no remaining actionable P1/P2 findings.
- Independent review found unsafe rejection cases; explicit regressions now cover
  in-place sort, dormant helpers, shadow bindings, numeric/lazy state, inline
  operations and child delegation.
- No database migration or operator environment change is required. API/worker
  images must include the updated locked parser dependencies.
- No provider/model calls, generation, publication, payment, customer-row writes,
  bot transfer or moderation change are part of this source change.

Full CI and canonical exact-revision production delivery are required before
announcing the guard deployed. The Windows coordinating owner performs deployment
serially; its receipt must record exact revision/images, health and end gates.
