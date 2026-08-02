# Visual Evaluation

This is not a template. Act as an independent reviewer after rendering; do not
defend an earlier decision merely because you made it.

## Score the screenshot from 1 to 5

1. **Brief fidelity** — audience, job and requested character are recognisable.
2. **Primary-action clarity** — the next action wins the first visual scan.
3. **Composition** — hierarchy, rhythm, density and alignment feel intentional.
4. **Visual character** — the result has a coherent product-specific thesis.
5. **Interaction truth** — controls, states and feedback look usable, not fake.
6. **Mobile craft** — 390px wraps, touch targets, safe areas and content priority
   hold without horizontal overflow.
7. **Completeness** — loading, empty, populated, error and success states belong
   to one system and no required flow ends abruptly.

## Review protocol

- Describe what the eye notices in the first three seconds.
- Identify the three highest-impact defects, separating functional blockers
  from aesthetic weakness.
- Compare the rendering to the chosen art-direction thesis, not a generic
  inspiration gallery.
- Remove any card, badge, gradient or animation without information value.
- Verify real Russian content, numerical alignment, image relevance and edge
  behaviour. Never award quality for hidden or unimplemented features.

## Exit gate

Fix every functional blocker and every axis below 4 before `done`. Re-run build,
runtime check and `see` after changes. If visual proof is unavailable, report it
honestly and rely on deterministic gates; never invent a passing screenshot.
