# Managed MoySklad order receipts

## Scope and contract

Additive to the existing `IntegrationOperation.result` JSONB; no migration,
historical rewriting, customer-wallet change, provider credentials, or generated
application data change. `POST /api/runtime/projects/{project}/orders` still
returns `provider` and `id`. New orders additionally retain a whitelisted price
snapshot from server preflight, committed before the provider write. Confirmed
success replaces the pending receipt once; subsequent replay returns that result.

`snapshot.amount_source = server_submitted` means the actual quantities and
prices sent by the platform, not an invoice, payment, fulfillment, or later
provider state. Quantity, unit price and totals are decimal strings; monetary
values use provider minor units and are not rounded to integers. Browser prices
and totals are ignored. Product names may be `null`; buyer contacts and raw
provider bodies are excluded.

Currency stays `null` before dispatch. Only a valid ISO-style code explicitly
present in the successful document response `rate.currency.isoCode` enriches
the receipt. Catalog currency does not establish document currency. Optional
`provider_total_minor` records a valid response `sum` separately. Missing or
malformed monetary fields do not invalidate a confirmed order ID or cause
another POST. No currency conversion is inferred.

Authenticated receipt reads use the existing signed MAX/core-assertion boundary:

| SDK method | Platform route | Meaning |
| --- | --- | --- |
| `getYleumOrders()` | `GET .../orders` | Latest 20 own receipts, with `has_more` |
| `getYleumOrderStatus(key)` | `POST .../orders/status` | Own durable intent status |
| `getYleumOrder(id)` | `POST .../orders/details` | Own confirmed order receipt |

All queries filter project, MAX actor, provider and operation kind before
returning results. No provider call, credential lookup, external reconciliation,
or write occurs on reads; own historical receipts remain readable after
disconnect. Foreign/missing key or order returns the same 404. Responses use
`Cache-Control: private, no-store`. Legacy receipts expose
`snapshot_status=unavailable` and explicit null details; no history is repriced.

Statuses describe platform dispatch only: `dispatching` and `unknown` remain
unresolved; `rejected` does not claim a provider-side cancellation. A missing
receipt is not proof that no external write occurred.

Generated UI must persist a project/actor-scoped intent key before submission,
keep that same key across unknown outcomes/reload, and only start a new intent
for an explicit new purchase after success. New history reads these receipts:
never create a second local order after the external order. Preserve old local
history separately as read-only. Portable code must not mint assertions or
contain credentials. Existing published apps are not automatically edited or
republished by this platform change; SDK/proxy refresh follows normal guarded
generation and promotion.

## Verification and delivery checkpoint

Base: `a67b7cd7856326178ffea99e39aae90c6ee389a6`. Work is isolated on
`feat/managed-order-receipts-20261010`.

Regression coverage uses disposable PostgreSQL and mocked provider transport:
server prices vs browser price, duplicate-line totals, unknown document currency,
malformed provider totals, durable pre-dispatch snapshot, ambiguous failure,
same-key replay, concurrent one-effect dispatch, legacy/null integration receipt,
foreign actor/project denial, latest-page limit, actual SDK calls and signed
proxy forwarding/body-tamper rejection. No live provider orders were created.
Independent review identified and closed catalog/document currency confusion
and recursive omission of nested nullable fields. Final bounded review: no
findings, 47 targeted tests passed in a separate review database. Root focused
API/template suite: 169 passed; explicit nullable-name/currency HTTP cases:
2 passed; Ruff clean and mypy clean across 282 source files. These suites overlap
and their counts must not be summed. Full API suite and exact CI remain in
progress at this checkpoint.

Production installation and real MAX → MoySklad checkout/reopen are pending.
The Windows QA/deployment owner serializes release after the current Coffee
generation reaches terminal state and fresh global idle/config preservation
checks. Do not treat local tests or a generated build as live acceptance.

Initial CI `38051425503` reported red image/export and orchestrator gates. The
strict materialization and API export verifiers were reproduced locally: they
expected the previous SDK/proxy bytes. A follow-up updates only those reviewed
file hashes/size/blob metadata in existing approved override fixtures, retaining
the historical golden and allowlists. Both local gates pass afterward (14
materialization/dependency tests plus the API export verifier); final exact CI
must still pass. Orchestrator Ruff/mypy are clean across 113 source files.

The broader local API run was stopped for diagnosis at 44 failures / 2278
passes; this is not a complete suite PASS. Every observed failure was in the
historical config-render aggregate after the two reviewed template changes.
Test-only normalization now pins the new exact SDK/proxy hashes, validates the
rendered project binding, and projects only those two files onto immutable base
`a67b7cd` sources before comparison with the unchanged aggregate golden. All 58
render tests pass; independent review confirms unexpected SDK/proxy and binding
drift remains rejected in both modes. Remaining local API cases and final full
CI are still required; no runtime acceptance guard is loosened.

Delivery must also rebuild the materialized MAX kit and its precompiled trusted
public/preview core through `docs/operations/project-cell-main-stack.md`, using
verified immutable ancestors and retaining previous pins for rollback. Updating
the API alone does not add the reserved proxy routes to an older compiled core.
Verify installed `order-list`, `order-status`, `order-details` without creating
a provider order, then normal guarded app promotion and genuine MAX acceptance.
