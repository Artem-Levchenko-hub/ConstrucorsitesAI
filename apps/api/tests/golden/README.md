# Golden samples

Specification-based regression seeds. Each `*.json` declares what a "good"
generation for that vertical looks like — preset candidates, palette
ranges, required sections, forbidden anti-patterns, recommended kit
classes, font pairings.

These are NOT screenshot comparisons (that's Phase D.4, deferred — needs
CLIP + Playwright wiring). They're structural assertions a future test
runner can check against actual generated HTML by:

* parsing the output HTML
* extracting palette HEX values from inline `tailwind.config` and CSS
* extracting font names from `<link href="...fonts.googleapis.com/...">`
* enumerating `<section>` elements and inferring their type by heading text
* enumerating omnia-kit classes used

For each assertion that fails, the runner logs a violation. CI fails if
violations cross a threshold (TBD — start at 0, allow flaky).

## Current goldens

| id | prompt class | created | catches |
|---|---|---|---|
| `apteka.json` | pharmacy with delivery | 2026-05-26 | indigo+violet on medical vertical, SaaS-tier pricing on apteka, missing pharmacist consultation section |
| `coffee.json` | coffee shop / cafe | 2026-05-26 | corporate-blue palette on warmth-vertical, 9-section overkill, SaaS-style pricing tiers |
| `saas-startup.json` | SaaS analytics | 2026-05-26 | missing integrations/case-studies, wrong palette warmth, kit classes not leveraged |

## Adding a new golden

1. Write a minimal `input.prompt` that reproduces a real-world ambiguous case.
2. Run it through the actual generation pipeline. If output is good, derive
   `expected.*` from what you see. If output is bad, derive `expected.*`
   from what you WANT to see + add the failure mode to `notes`.
3. Keep `palette_range.must_avoid_any_of` populated — anti-examples catch
   regressions faster than positive examples.

## Future Phase D.4 — CLIP regression

When Phase D.4 lands (`scripts/test-design-quality.py`):
* Each golden gets a `screenshot/` directory next to its `.json`.
* Initial run captures the "good" screenshot via Playwright at 1440×900.
* CI generates fresh screenshots per golden, computes CLIP-embedding
  cosine similarity to the stored reference, fails if < 0.75.
* Regenerate screenshots monthly (or on intentional design-shift commits)
  via a manual `--update-goldens` flag.
