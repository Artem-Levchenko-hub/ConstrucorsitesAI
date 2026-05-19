---
title: "Code View and Snapshot Management"
tags: [animation, code-editor, frontend, react, snapshot-management, uiux, version-control]
sources:
  - daily/2026-05-17.md
created: 2026-05-19
updated: 2026-05-19
---

# Code View and Snapshot Management

Introduction of a dedicated Code View for snapshots, a 'viewing old version' banner, and compact snapshot cards for improved navigation and context.

## Key Points

- New `CodeView.tsx` displays file tree and code with copy/download.
- PreviewFrame includes a tab switch for 'Preview' / 'Code'.
- Amber banner indicates 'viewing old version' with a link to current.
- Snapshot cards are made compact with hover-scale animation.
- Workspace store updated with `viewMode` and `setViewMode` action.
- New API function `getSnapshotWithFiles` to fetch snapshot data.

## Details

A new `CodeView.tsx` component was developed to provide a dedicated interface for viewing the code generated in a snapshot. It features a file tree on the left, sorted by file size, and a `<pre>` block on the right for displaying code, complete with copy and download buttons. This view pulls data from `GET /api/projects/:pid/snapshots/:sid`.

The `PreviewFrame` now includes a tab-switcher allowing users to toggle between 'Preview' and 'Code' views. Device toggle and reload buttons are hidden when in 'Code' mode to streamline the interface for code inspection. This enhances the utility of snapshots beyond just visual previews.

To improve user awareness, an amber banner is displayed when a user is viewing an older snapshot version, indicating 'Просматриваете старую версию (X назад) — sha' and providing a convenient link to 'Вернуться к текущей →'. This helps users quickly navigate back to the latest state.

Snapshot cards were made more compact, reducing prompt text to one line and badge sizes. A `whileHover={{ scale: 1.04 }}` animation with `cubic-bezier(0.16,1,0.3,1)` was added, making cards subtly enlarge and lift (`hover:z-10 + shadow-xl`) on hover, improving visual feedback and navigation.

## Related Concepts

- [[knowledge/concepts/secondbrain-runtime]]
- [[knowledge/concepts/daily-ingestion-process]]

## Sources

- [[daily/2026-05-17.md]]

## Backlinks

- [[connections/code-view-and-snapshot-card-integration]]
