# Interaction & Motion

This is not a template. Motion is the behaviour of the product over time, not a
layer of effects added after layout.

## State inventory

For every primary action define idle, pressed, pending, committed, success,
recoverable error, disabled and interrupted states. Feedback appears at the
point of action and preserves enough context to recover.

## Spatial choreography

- Use shared position, scale or container continuity when an object changes
  detail level so users understand where content came from.
- Bottom sheets are for short contextual decisions. Keep the source visible,
  provide a title and close path, preserve drafts and avoid nested sheets. Use a
  full screen for deep focus or complex input.
- Use lateral transitions for peers, vertical or container expansion for depth,
  and restrained fades for replacement.
- Skeletons reserve final geometry. Progress distinguishes indefinite waiting
  from determinate work.

## Motion tiers

- 90–160ms for press, toggle and local acknowledgement.
- 180–280ms for sheets, expansion and component state change.
- Up to 500ms for one interruptible completion signature.

Prefer transform and opacity. Never use catch-all transitions or perpetual
decoration. Animation must stop cleanly when state changes quickly.

## Haptics and reduced motion

Use supported MAX haptics only for meaningful selection, success or warning.
Under `prefers-reduced-motion`, replace travel with immediate state changes or
short opacity transitions while preserving acknowledgement and focus order.

Test rapid repeat taps, back during pending work, sheet dismissal with a draft,
slow network, keyboard focus and reduced motion. Correctness must not depend on
animation completion.
