---
title: "Omnia.AI Generation Quality — 4 Regressions Fixed 2026-05-26"
tags: [ai-generation, omniaai, prompt-engineering, proxyapi, design-presets, regressions, root-cause]
sources:
  - daily/2026-05-26.md
created: 2026-05-26
updated: 2026-05-26
---

# Omnia.AI Generation Quality — 4 Regressions Fixed 2026-05-26

User-visible complaint: `/p/sait-apteki-53a12d` (Flowapteka project) rendered
as a generic indigo+violet SaaS landing — wrong palette for a pharmacy, wrong
copy structure (SaaS-style "Basic/Standard/Premium" tiers for a drugstore),
literal `"0"` stuck in the stats counter spans, and a "Модель `claude-haiku-4-5`
вернула пустой ответ (4 символов). Переключаюсь на `gpt-5-nano`…" notice in
the chat. Four separate root causes, all fixed in 5 commits.

## Key Points

- **proxyapi.ru cold-start returns near-empty stream** on first request after
  >5 min idle. Visible in both `preset_classifier` (silently defaulted to
  `editorial-trust`) and the main generation flow (paid for empty Haiku +
  gpt-5-nano fallback). Warmup loop in `services/warmup.py` keeps the route
  hot every 240s; in-band retry in `litellm_router.acompletion` catches misses.
- **Brand names with industry signal buried inside one latin token** —
  "Flowapteka", "MedClinic", "DentalStudio" — missed every existing matcher
  because all of them used `startswith`. The historical comment in
  `prompt_builder._expand_ru_to_en` explains the safeguard: short prefixes
  like "бар" once routed "Барнаул" to Restaurant. Substring matching for the
  full token wasn't safe for short stems. **Fix**: new
  `_INDUSTRY_FRAGMENT_TO_PRESET` dict in `preset_classifier.py` — only
  ≥5-char unambiguous fragments matched via substring, both cyrillic
  ("аптек", "стомат") and latin ("aptek", "pharm", "clinic", "dental"),
  ranked before the existing scoring pass.
- **Haiku 4.5 with ~1200-line system prompt reverts to training-default
  palette** (indigo `#4f46e5` + violet — the Vercel/Linear/shadcn aesthetic).
  `format_preset_block()` injects the correct palette mid-prompt, but the AI
  ignores it from that depth. **Fix**: `_format_palette_anchor()` injected at
  position #2 (right after `_IDENTITY`, before `_QUALITY_BAR`) — short,
  imperative, names the forbidden HEX values explicitly.
- **`IntersectionObserver` with `threshold: 0.4` does not reliably fire its
  initial entry** for elements that are already in the viewport at init —
  especially when wrapped in a `.reveal` container that starts at
  `opacity: 0`. Above-fold counters (`<span data-count-to="3200">0</span>`)
  stayed literal "0". **Fix**: after `observe()`, one
  `requestAnimationFrame()` kick that calls `runCount()` directly on any
  counter whose bounding rect is above 60% of viewport height. Applied
  identically to all 4 static template kits (blank/blog/landing/portfolio).

## Cascading failure pattern

The 4 bugs compounded. Cold-start empty response from proxyapi.ru caused the
preset classifier to default to `editorial-trust` — but `editorial-trust`'s
palette is B&W, not indigo+violet. Indigo came from the AI's training-data
default winning over the buried `format_preset_block` declarative. The
counter "0" was an independent rendering bug. The user-facing fallback
notice was a real symptom of an unrelated proxyapi cold-start hitting the
main generation flow.

So a single broken site combined: wrong-preset-selection (bug 1+2),
right-preset-but-AI-ignored-palette (bug 3), and a rendering issue (bug 4).
Fixing only one would have left the user complaining about the others.

## Invariants to maintain

- **Industry fragments dict has a strict contract**: each fragment must be
  ≥5 chars, unambiguous (no "med" — matches "mediator"), and map to ONE
  preset_id from `PRESETS`. Both cyrillic and latin variants needed because
  RU brands routinely latinize roots ("Flowapteka", "PharmExpress").
- **Mid-prompt declarative blocks lose authority** as system prompt grows.
  Anything CRITICAL for output (palette, fonts, content structure) must be
  anchored within the first ~1k tokens, ideally right after the identity
  block.
- **Warmup loop must survive every transient upstream failure** — exceptions
  in `_ping()` are logged, never raised. A single proxyapi 500 must not
  kill the entire warmup task.
- **Empty-response retry is in-band protection only** — the proactive fix
  is warmup. If retry catches more than ~5% of acompletion calls, warmup
  is failing.

## Files changed

| Commit | File | Purpose |
|---|---|---|
| 91a78cd | `apps/llm-gateway/src/omnia_gateway/{main.py, services/warmup.py}` | Warmup loop |
| f81c7e9 | `apps/llm-gateway/src/omnia_gateway/services/litellm_router.py` | Retry on <50 chars |
| 7b8569b | `apps/api/src/omnia_api/services/preset_classifier.py` | Substring match + classifier retry |
| aeae7b9 | `apps/api/src/omnia_api/services/prompt_builder.py` | `_format_palette_anchor` |
| adf129e | `apps/api/src/omnia_api/templates/{blank,blog,landing,portfolio}/assets/omnia-kit.js` | Counter kick |

## Related Concepts

- [[knowledge/concepts/proxyapi-anthropic-route]] — proxyapi.ru Haiku routing
- [[knowledge/concepts/omniaai-generated-site-design-presets-awwwards-v3]] — preset system
- [[knowledge/concepts/omniaai-generation-rules]] — generation rules SOT
- [[knowledge/concepts/omniaai-design-references-and-principles]] — design playbook

## Sources

- [[daily/2026-05-26.md]]
- User session: live audit of `https://constructor.lead-generator.ru/p/sait-apteki-53a12d`
