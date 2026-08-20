# MAX UI product design — exact template contract

This template pins `@maxhub/max-ui@0.2.0` with React 18. Build the product as a
MAX Mini App, not as a generic website, Telegram/VK app or marketing landing.

## Existing platform shell

- `src/app/layout.tsx` already imports `@maxhub/max-ui/dist/styles.css`.
- `src/components/MaxAppProvider.tsx` already wraps the app in `MaxUI` and maps
  MAX appearance to `platform="ios" | "android"` and
  `colorScheme="light" | "dark"`.
- Reuse that provider. Do not add a second `MaxUI`, bridge bootstrap or global
  MAX UI stylesheet import.
- Read and preserve the root `DESIGN.md`. Its `--app-*` tokens own product
  surfaces and composition; MAX UI owns its internal semantic control tokens.

## Verified public imports

Use public exports that exist in the pinned package:

`Avatar`, `Button`, `CellAction`, `CellHeader`, `CellInput`, `CellList`,
`CellSimple`, `Counter`, `IconButton`, `Input`, `MaxUI`, `Spinner`, `Textarea`,
`Typography` and the package's exported icons/hooks.

This contract is the dependency inspection result. Do not browse `node_modules`,
re-read package declarations or probe exports before writing the requested main
screen; that duplicates verified work and consumes the pre-write budget.

- `Button`: sizes `xsmall | small | medium | large`; variants
  `primary | secondary | ghost | primary-contrast | secondary-contrast |
  overlay | destructive`; supports `stretched`, `iconBefore`, `iconAfter`,
  `indicator`, `loading`.
- `IconButton`: the same variants/sizes; always provide an accessible label.
- `Input`: modes `default | contrast`; sizes `medium | large`; supports icons,
  clear button, count and hint.
- `Textarea`: modes `primary | secondary`.
- `CellList`: modes `full-width | island`; use `filled` and `header` deliberately.
- `CellSimple`: supports title/subtitle/overline, before/after, chevron,
  separator, disabled and link states.
- `CellAction`: modes `primary | destructive | custom`; heights
  `compact | normal`.

Do not import `Panel`, `Grid`, `Container`, `Flex` or `TabBar`: they are not
public exports in `0.2.0`. Build layout with semantic HTML/CSS. Build bottom
navigation with accessible buttons/links and `lucide-react` icons. Avoid new
usage of deprecated `Switch` and `ToolButton`.

## Product quality floor

- Start with the requested main screen and primary journey before auxiliary
  files. Use 3–5 bottom destinations only when the information architecture
  needs them.
- One visually dominant action per screen. Use hierarchy, spacing and type
  scale instead of wrapping every item in the same card.
- Every data surface has useful loading, empty, error and success states.
- Use realistic Russian copy and representative data. Every visible control
  performs an action or is clearly disabled with an explanation.
- At 360–390px: no horizontal/nested scroll; touch targets are at least 44px;
  safe areas and the keyboard do not cover the active control or CTA.
- Use one consistent `lucide-react` stroke style for icons. Never use emoji as
  interface icons or hand-drawn SVG replacements.
- Run `pnpm typecheck` and `pnpm build`; repair only concrete errors, then stop.
