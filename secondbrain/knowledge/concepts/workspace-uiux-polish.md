---
title: "Workspace UI/UX Polish"
tags: [framer-motion, frontend, nextjs, optimistic-ui, react, tailwind-css, uiux]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# Workspace UI/UX Polish

Implemented several UI/UX improvements in the workspace, focusing on better model selection, dynamic snapshot cards, optimized layout, and an optimistic UI update for prompt submission.

## Key Points

- Improved `ModelSelector` dropdown with fixed height and scrollability.
- Created dynamic `SnapshotCard` with `framer-motion` for compact display and hover-based expansion.
- Adjusted `Workspace` grid layout to give more space to the preview iframe.
- Streamlined `Timeline` header and text for conciseness.
- Implemented optimistic insert for prompt messages, reducing perceived latency after submission.

## Details

The `ModelSelector.tsx` was enhanced by adding `max-h` and `overflow-y-auto` to the dropdown content, ensuring all models, especially newly added Gemini ones, are accessible without being cut off by the viewport. The header was made sticky for better navigation.

The `SnapshotCard.tsx` was refactored to be compact by default, using `aspect-[16/7]` and smaller text. On hover, `framer-motion`'s `layout` prop smoothly expands it to `aspect-[16/9]` and reveals the 'Rollback' button, avoiding layout shifts caused by CSS `transform: scale`.

The `Workspace.tsx` grid layout was adjusted, reducing the right-hand history column from 320px to 220px, thereby allocating more horizontal space to the central preview iframe, improving the user's view of the generated website.

Minor aesthetic and functional tweaks were made to `Timeline.tsx`, including reducing header height and text size, and simplifying the 'N versions' label to just 'N' for a cleaner look.

## Related Concepts

- [[knowledge/concepts/frontend-agent-brief]]
- [[knowledge/concepts/platform-experience-plan]]
- [[knowledge/concepts/design-system]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/api-stability-and-performance-directly-impacts-user-experience-and-platform-reliability]]
- [[connections/gemini-integration-and-geo-blocking-solution-enables-new-llm-capabilities-for-users]]
- [[connections/monorepo-structure-and-agent-briefs-guide-development-and-maintain-consistency]]
