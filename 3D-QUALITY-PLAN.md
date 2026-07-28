# 3D quality follow-up

Shipped in this change: one platform-wide depth contract, managed static WebGL
runtime, catalog hero wiring, source-level anti-flat gate, and agent feedback.

Remaining follow-up (not required for this rollout):

- Calibrate three visual variants on a 10–20 prompt cross-industry dogfood set
  and record GPU/frame-time on low-end Android.
- Add the managed runtime directly to container starter layouts so Next/Vite
  writers can use the same zero-dependency primitive instead of authored canvas.
- Add a rendered browser probe that confirms WebGL context creation and the
  reduced-motion fallback, complementing the deterministic source gate.
- Version managed kit assets and migrate untouched legacy snapshots on open,
  rather than only on their next full generation.
