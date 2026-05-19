---
title: "Zero-Files Silent Failure Fix"
tags: [api, bug-fix, error-handling, file-extraction, llm-gateway, uiux]
sources:
  - daily/2026-05-17.md
created: 2026-05-19
updated: 2026-05-19
---

# Zero-Files Silent Failure Fix

Resolution of a silent failure mode where `extract_files()` returning an empty object would lead to an uninformative UI state, now providing explicit error feedback.

## Key Points

- Previously, `extract_files()` returning `{}` caused silent `llm.done` without snapshot.
- UI appeared 'OK' but preview didn't update.
- Fix: `apps/api/.../routers/messages.py:308` now sends `llm.error`.
- Error message suggests using Haiku/Sonnet for file generation.
- Ensures UI provides clear feedback on file generation issues.

## Details

Before this fix, if the `extract_files()` function returned an empty object (indicating no files were generated or extracted), the system would silently proceed to `llm.done` without creating a snapshot. This resulted in a confusing user experience where the UI appeared to be 'all OK,' but the preview frame remained unchanged, giving no indication of the underlying issue.

The fix addresses this by modifying `apps/api/.../routers/messages.py:308`. Now, when `extract_files()` returns an empty object, an `llm.error` message is explicitly sent. This error includes a helpful suggestion to the user about potentially using models like Haiku or Sonnet, which are known for their file generation capabilities.

This change ensures that users receive immediate and actionable feedback when file generation fails or results in no output, preventing silent failures and improving the overall transparency and usability of the system. It transforms a confusing 'nothing happened' scenario into a clear error state with guidance.

## Related Concepts

- [[knowledge/concepts/knowledgeconceptszero-files-silent-failure]]
- [[knowledge/concepts/knowledgeconceptsfile-extractor-pipeline]]

## Sources

- [[daily/2026-05-17.md]]

## Backlinks

- [[connections/haiku-integration-and-zero-files-fix-synergy]]
