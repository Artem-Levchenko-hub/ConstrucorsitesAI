---
title: "Enhanced Chat UI and Prompt Queueing"
tags: [chat-interface, frontend, prompt-management, react-query, streaming, uiux]
sources:
  - daily/2026-05-17.md
created: 2026-05-19
updated: 2026-05-19
---

# Enhanced Chat UI and Prompt Queueing

Improvements to the chat user interface including compact file chips, non-blocking input during streaming, and a robust prompt queuing mechanism.

## Key Points

- ChatMessage parses `<file>` blocks into compact, expandable chips.
- PromptInput textarea is no longer disabled during streaming.
- Cancel function now marks assistant message as completed in cache.
- Prompt submission during streaming adds to `pendingRef` queue.
- Queue automatically fires on `llm.done` or `llm.error`.
- UI shows 'Stop' and 'Send/Queue' buttons simultaneously, with a 'Queued' banner.

## Details

The `ChatMessage` component was enhanced to parse `<file>` blocks from assistant responses using `lib/parse-assistant.ts`. These files are rendered as compact chips that can be expanded on click, displaying their size in KB. This provides a cleaner and more informative chat interface.

User experience during streaming was improved by removing the `disabled` attribute from the `PromptInput` textarea, allowing users to type new prompts while the model is still generating a response. This reduces perceived latency and improves interactivity.

A significant update to `usePromptStream`'s `cancel()` function now not only closes the WebSocket but also immediately marks the current assistant message as completed in the React Query cache. This instantly unlocks the UI. Furthermore, `llm.error` now also sets `tokens_out: 0`, ensuring the UI unblocks on errors as well.

A prompt queuing mechanism was introduced: if a user submits a new prompt while a stream is active, it's placed in a `pendingRef` (single slot). Upon `llm.done` or `llm.error`, this queued prompt is automatically fired via `setTimeout` to prevent recursion. The UI clearly indicates when a prompt is queued with a banner and allows users to cancel it.

## Related Concepts

- [[knowledge/concepts/secondbrain-runtime]]
- [[knowledge/concepts/daily-ingestion-process]]

## Sources

- [[daily/2026-05-17.md]]

## Backlinks

- [[connections/code-view-and-snapshot-card-integration]]
- [[connections/streaming-preview-and-chat-ui-enhancements]]
