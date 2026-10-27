# Publish the last accepted edit after a failed generation

The live QA update fails at `_prepare_locked` before considering its valid saved
checkpoint: request accepted epoch 60, live machine epoch 62, workspace fence 63.
The accepted source revision matches the sealed archive; the public app remains
on the preceding release and retains its 630 RUB order.

- Reproduce with a real warm-archive fixture and a later machine epoch (RED).
- Resolve the saved warm seal before the historical machine-epoch guard. Require
  the old equality for all live capture and cold seed paths. Keep owner, current
  fence, source revision, restoration proof, manifest, archive hash validation
  and production schema compatibility unchanged.
- Cover missing/stale/incomplete/corrupt archives, current identity/fence/busy
  failures, cold publication, schema drift and the unchanged same-epoch fallback.
- Verify focused orchestrator tests, Ruff/type checks, independent review and CI.
- Merge and deploy the exact green main SHA through the canonical driver, also
  including the already-green commercial price cutover from PR143.
- Retry publication through the UI, freshly open actual MAX, verify new prices,
  navigation, all prior edits, old order630 and new order650. Update the report.
