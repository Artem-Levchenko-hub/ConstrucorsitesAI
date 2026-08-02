# Domain Intelligence — Booking

This is not a template. A booking product sells confidence that the right
service, person and time are truly reserved.

## Domain objects and loop

Service, resource or specialist, location, slot, participant, booking and policy
must not collapse into one card. The loop is choose service → narrow available
options → select slot → confirm details → receive durable confirmation.

- Availability must come from a real source; never render invented open slots as
  if they are bookable.
- Hold or revalidate a slot before final confirmation. Prevent duplicate submit
  and explain when another user took the slot.
- Keep timezone, duration, location and price visible near the decision.
- Support reschedule, cancellation, no-availability and waitlist only when real
  behaviour exists.
- Preserve user input when returning from policies, consent or payment.

The first-run state shows services or an honest integration requirement, not
fake appointments. User booking history is scoped to verified MAX identity.
Activation is the first confirmed booking, not selecting a date. Payments and
contact/health fields trigger `trust-safety`.
