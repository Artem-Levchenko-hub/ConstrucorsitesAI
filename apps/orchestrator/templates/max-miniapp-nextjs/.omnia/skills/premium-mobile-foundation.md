# Premium mobile foundation for MAX

This is a deterministic interaction and accessibility contract, not a template
or reusable visual skeleton. Preserve the selected DesignDNA: use these primitives to make its
direction reliable without turning every product into the same dashboard.

## Primitive contracts

- **Navigation:** use semantic `nav`, `button` and links; show the current view
  with text/shape as well as colour. Keep a stable content spacer for bottom
  navigation and safe areas. Back always returns to a predictable prior state.
- **Sheets and dialogs:** label the surface, trap/restore focus, support Escape
  and backdrop close when safe, lock background scroll, and keep the primary
  action reachable above `env(safe-area-inset-bottom)`.
- **Forms:** every control has a visible label, input purpose and inline error.
  Submission exposes pending/disabled/error/success states and never confirms
  before the awaited managed operation resolves.
- **Charts:** use real or explicitly empty data, direct labels and a text
  alternative. SVG/CSS marks need an accessible name; colour is never the sole
  encoding. Numbers use tabular figures. Avoid desktop axes squeezed into 360px.
- **States:** loading preserves layout, empty teaches the first useful action,
  error explains recovery, success confirms the real result and offers the next
  move. Offline/unavailable is distinct from empty.
- **Touch and focus:** interactive targets are at least 44×44 CSS px with a
  visible pressed state and `:focus-visible` treatment. Do not nest controls.
- **Safe area and keyboard:** account for top/bottom insets, viewport resize and
  the software keyboard. Fixed docks require an opaque surface and matching
  scroll spacer; they may not cover content.
- **Motion:** animate transform/opacity first, keep interaction motion causal and
  interruptible, and provide a useful `prefers-reduced-motion` equivalent.

## Pattern catalog (choose, do not copy all)

- task-first single canvas with contextual controls;
- compact bottom tabs for 3–5 peer views;
- segmented control/chips for local mode changes;
- progressive disclosure or accessible sheet for secondary detail;
- inline async result and retry near the action that caused it;
- directly labelled sparkline/progress ring only for truthful data;
- optimistic acknowledgement only when rollback and error truth are implemented.

The domain skill and selected DesignDNA decide composition, type, geometry,
colour and signature interaction. This foundation only guarantees that those
choices remain usable, truthful and native-feeling at 360px and 390px.
