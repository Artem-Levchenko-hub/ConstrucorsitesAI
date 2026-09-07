# Apply saved app data to product screens

The four Studio sections persist configuration, but existing generated screens
may contain their own constants. Saving configuration cannot implement arbitrary
features or rewrite those screens. Keep model-free saving and add an explicit
application action using the normal generation pipeline.

1. Explain the scope of save vs application for all four sections.
2. Save first; review the saved version and balance cost before AI dispatch.
3. Reuse a version-specific request key and reject stale versions under the
   project lock before model dispatch. Preserve accepted request replay.
4. Provide a typed uncached business-config reader and require generated screens
   to use it for owner-editable data. Refresh managed age marking too.
5. Test save failures, explicit launch, stale data, replay and fresh config reads.
   Verify web types/lint/build and affected API/managed-kit contracts.
6. Commit, push, deploy through production `full` compose, prove health and
   exercise a dedicated QA app. Record exactly what was verified.

The action does not claim that checking payment/marketing flags connects a
provider. Published source-based apps still require publication after code edits.
