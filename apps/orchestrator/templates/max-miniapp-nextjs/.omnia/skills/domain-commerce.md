# Domain Intelligence — Commerce

This is not a template. Build a trustworthy path from product understanding to
fulfilled purchase.

## Domain objects and loop

Product, variant, availability, price, cart line, order, payment and fulfilment
are separate states. The loop is discover → compare or inspect → choose valid
variant → confirm total → pay or submit → track.

- Product claims, price and availability come from business config or a managed
  catalog, not generated inventory.
- Variant selection updates images, price and stock together. Disable invalid
  combinations with an explanation.
- Cart totals distinguish item, discount, delivery and final charge.
- Make pending, paid, failed, cancelled and refunded states explicit and
  idempotent. Never show success before provider confirmation.
- Preserve the cart across navigation and retry; prevent duplicate orders.

New users see an honest empty cart and empty order history. Personal order data
is always scoped to verified MAX identity. Payments, delivery contacts, reviews
or marketplace content trigger `trust-safety`. Activation is a valid first cart
or confirmed purchase according to the product promise.
