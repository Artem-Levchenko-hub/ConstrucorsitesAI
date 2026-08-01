# Mobile motion and interaction choreography

Motion communicates causality, hierarchy and spatial continuity. It is not a
layer of decoration applied after the layout.

- Define a motion language with three tiers: micro feedback (roughly 80–160ms),
  local state change (160–280ms), and navigation/sheet transition (220–420ms).
  Tune by feel; do not make every element share one duration.
- Give touch immediate acknowledgement through pressed state and, when supported,
  restrained MAX haptics. Never wait for a network response before feedback.
- Preserve object continuity: a tapped card may expand into detail; a selected
  segment moves its indicator; inserted or removed items animate from their
  causal location.
- Animate `transform` and `opacity` first. Avoid layout-thrashing animation,
  scroll listeners that perform synchronous work, and simultaneous motion across
  the whole screen.
- Choreograph loading: skeleton geometry should match final content; cross-fade or
  morph to content without jumping the page. Progress must reflect real state.
- Sheets and overlays need focus/scroll discipline, a dimmed context layer, a
  reachable dismissal gesture/action and safe-area padding.
- Respect `prefers-reduced-motion`: remove travel, parallax and looping movement
  while preserving state changes through instant opacity/colour/shape feedback.
- Test at 360px with touch assumptions. Hover may be absent and must carry no
  meaning.

One memorable transition serving the primary loop is worth more than ten generic
fade-ins.
