---
title: "Real-time Streaming Preview with morphdom"
tags: [css-animation, frontend, html-parsing, iframe, morphdom, real-time, streaming, uiux]
sources:
  - daily/2026-05-17.md
created: 2026-05-19
updated: 2026-05-19
---

# Real-time Streaming Preview with morphdom

Implementation of a live, per-element streaming preview for generated HTML content using a long-lived iframe and morphdom for efficient DOM patching.

## Key Points

- Introduced `StreamingPreviewFrame.tsx` with a persistent iframe.
- Uses `BOOTSTRAP_HTML` with Tailwind, morphdom, and animation CSS.
- Bootstrap script listens for `omnia:render` messages to update content.
- morphdom patches `document.body` with `childrenOnly` and `skip-if-equal`.
- New elements receive `data-omnia-new` for fade+slide-up animation.
- CSS updates without flicker via `document.getElementById('omnia-css').textContent`.

## Details

The `StreamingPreviewFrame.tsx` component creates a long-lived iframe initialized with `BOOTSTRAP_HTML`. This HTML includes essential libraries like Tailwind CDN, morphdom UMD CDN, and custom animation CSS, along with a dedicated `<style id="omnia-css">` slot for dynamic CSS updates.

A bootstrap script within the iframe listens for `window.message` events of type `omnia:render`. Upon receiving `bodyHtml` and `cssText`, it uses `DOMParser` to construct a new body. `morphdom` is then employed to efficiently patch the iframe's `document.body`, ensuring only necessary changes are applied and avoiding full re-renders.

New top-level children introduced by `morphdom` are tagged with `data-omnia-new`, triggering a smooth fade-in and slide-up animation. CSS updates are handled by directly modifying the `textContent` of the `<style id="omnia-css">` element, which prevents visual flickering. The preview debounces updates with a 150ms delay for performance.

## Related Concepts

- [[knowledge/concepts/knowledgeconceptsrealtime-streaming-preview]]

## Sources

- [[daily/2026-05-17.md]]

## Backlinks

- [[connections/streaming-preview-and-chat-ui-enhancements]]
