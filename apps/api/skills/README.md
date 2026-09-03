# Vendored AI Generation Skills

External design / UX knowledge bundles vendored into the API repo so prod
LLM-gateway nodes ship with deterministic, version-pinned content.

Loaders live at `apps/api/src/omnia_api/services/skill_library.py`.

## `ui-ux-pro-max/`

Source: [`nextlevelbuilder/ui-ux-pro-max-skill`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill),
version `2.13.0`, commit `8a1a6d857332da32252d77365da90c3f6293b47b`
(2026-08-19). Exact provenance is recorded in `SOURCE.json`; the MIT terms are
preserved in `LICENSE`.

What's inside (`data/`):

| File | Rows | Use |
|---|---:|---|
| `colors.csv` | 192 | WCAG-safe palettes by product type (SaaS, e-com, healthcare, fintech, gaming, …). Each row: primary / accent / background / foreground / card / muted / border / destructive / ring. |
| `typography.csv` | 74 | Font pairings (heading + body) with Google-Fonts import URL + Tailwind config snippet + mood/best-for keywords. |
| `ux-guidelines.csv` | 119 | UX rules across Navigation / Animation / Forms / Loading / Mobile / Accessibility / Performance. Each row: do / don't + code example + severity. |
| `styles.csv` | 88 | Visual style presets (glassmorphism, brutalism, bento, neumorphism, …). |
| `products.csv` | 192 | Product-type reasoning rules. |
| `charts.csv` | 25 | Chart-type recommendations by data-shape. |
| `motion.csv` | 17 | Motion principles and interaction patterns. |
| `stacks/*.csv` | 22 | Framework-specific implementation guidance. |
| `google-fonts.csv` | ~1k | Full Google Fonts catalogue (largest file at 728 KB). |
| `icons.csv`, `landing.csv`, `app-interface.csv`, `design.csv`, `ui-reasoning.csv`, `react-performance.csv` | — | Supplementary tables. |

`scripts/` ships upstream's own helpers (`core.py`, `design_system.py`,
`search.py`). They aren't imported by the API — `skill_library.py` reads
CSVs directly so the surface stays narrow.

## License

`ui-ux-pro-max` is MIT-licensed. The upstream notice is preserved verbatim in
`ui-ux-pro-max/LICENSE`.

## Integration status

- **Vendored** — files are in the repo, deploy artefacts include them.
- **Loader live** — `skill_library.lookup_palette`, `lookup_font_pairing`,
  `random_ux_guidelines`, `format_design_brief` ready to call.
- **Static/freeform wiring** — live through
  `services.prompt_builder._compute_skill_brief`.
- **Container-agent wiring** — live through `services.design_plugin`: one
  versioned pre-build product/UX contract is injected into the existing agent
  pass and persisted as `DESIGN.md`; generation has no `see` tool or visual-judge loop. The
  MAX template also injects a package-exact `@maxhub/max-ui@0.2.0` skill. The path intentionally
  selects only app-safe UX/icon/chart rows; it does not execute upstream scripts,
  inject landing-page structure, call a model/network, or add a completion phase.
