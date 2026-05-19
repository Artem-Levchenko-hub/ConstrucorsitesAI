---
title: "Workspace UI Polish and Optimistic Insert"
tags: [framer-motion, frontend, optimistic-ui, performance, react, uiux]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# Workspace UI Polish and Optimistic Insert

Implemented several UI/UX improvements in the workspace, including better model selector dropdowns, dynamic snapshot cards, adjusted column layouts, and an 'optimistic insert' for chat messages to improve perceived responsiveness.

## Key Points

- Model selector dropdown now scrolls and has a sticky header, making all models accessible.
- Snapshot cards are compact by default, expanding on hover with `framer-motion`.
- Workspace layout adjusted to give more space to the preview iframe.
- Timeline header made more compact, simplifying version count display.
- Optimistic insert for chat messages: temporary user/assistant messages added immediately after submit.
- Optimistic insert reduces perceived delay after sending a prompt from 1-2 seconds to 0 seconds.

## Details

UI improvements in `apps/web` included enhancing `ModelSelector.tsx` with `max-h` and `overflow-y-auto` for better scrollability and a sticky header. `SnapshotCard.tsx` was refactored to be compact by default, expanding smoothly on hover using `framer-motion`'s `layout` prop, avoiding layout shifts caused by `transform: scale`.

The `Workspace.tsx` layout was adjusted to `gridTemplateColumns: '320px minmax(0, 1fr) 220px'`, giving more horizontal space to the preview iframe. `Timeline.tsx` saw minor aesthetic changes to its header. The most significant UX improvement was the 'optimistic insert' in `hooks/usePromptStream.ts`, where temporary user and assistant messages are immediately added to the cache after submission, providing instant visual feedback and reducing perceived latency.

## Related Concepts

- [[knowledge/concepts/frontend-performance-optimization]]
- [[knowledge/concepts/responsive-ui-design]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/deployment-stability-and-startup-performance]]
- [[connections/end-to-end-validation-of-new-features]]
