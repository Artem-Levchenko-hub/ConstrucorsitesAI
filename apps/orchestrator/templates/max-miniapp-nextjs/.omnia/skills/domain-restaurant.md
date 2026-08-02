# Domain Intelligence — Restaurant

This is not a template. Design around deciding, ordering and knowing what will
happen next.

## Domain objects and loop

Menu section, item, modifier, availability, cart, fulfilment, order and status
are distinct objects. The primary loop is discover → customise → confirm → pay
or submit → track.

- Show price, portion or size, allergens and availability before commitment.
- Modifiers must expose required versus optional choices and update totals
  immediately.
- Preserve the cart across navigation and MAX WebView interruption.
- Prevent unavailable items, invalid combinations and duplicate submission.
- Make pickup/delivery/table context explicit; never imply live availability
  without a real integration.

## Operational truth

Catalog content comes from `omniaMaxConfig.content` or the managed catalog
integration, not invented stock. User orders are authenticated persisted
actions. Empty order history is honest. Confirmation includes order summary,
price, fulfilment expectation, cancellation/support path and payment state.

Payments and allergens trigger `trust-safety`. Activation is a completed valid
cart or first order, depending on the brief; retention comes from useful repeat
ordering, favourites or timely status, not manipulative urgency.
