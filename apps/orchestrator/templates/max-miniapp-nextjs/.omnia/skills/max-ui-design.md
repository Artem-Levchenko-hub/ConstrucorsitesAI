# MAX UI product design — exact template contract

This template pins `@maxhub/max-ui@0.2.0` with React 18. Build the product as a
MAX Mini App, not as a generic website or Telegram/VK app. The owner's explicit
product and visual brief determines screens, colors, fonts and style; general
craft advice must not replace it with an industry recipe. Platform security,
SDK contracts and verified component APIs remain required.

## Existing platform shell

- `src/app/layout.tsx` already imports `@maxhub/max-ui/dist/styles.css`.
- `src/components/MaxAppProvider.tsx` already wraps the app in `MaxUI` and maps
  MAX appearance to `platform="ios" | "android"` and
  `colorScheme="light" | "dark"`.
- Reuse that provider. Do not add a second `MaxUI`, bridge bootstrap or global
  MAX UI stylesheet import.
- Read the actual source and root `DESIGN.md`; ordinary edits preserve the incumbent
  design. An explicit owner redesign may update them together. Product `--app-*`
  tokens own surfaces and composition; MAX UI owns its internal control tokens.
- On a first build, an owner-led fallback typeface is a safe starting point, not
  the owner's final choice. Select available local/system typography for the
  product when that axis is free; keep typography metadata, CSS and pinned font
  assets consistent. Change dependencies only when the runtime permits; otherwise
  use available fonts and retain protected files. Explicit owner choices remain authoritative.
  Ordinary edits and restoration of a finished product preserve its established design
  and typography. An unfinished first build must still implement the original request
  and prepared visual plan: a seed fallback or intermediate draft is not an accepted
  final design. Complete the open visual choices while preserving working code and
  explicit owner preferences.

## Verified public imports

Use public exports that exist in the pinned package:

`Avatar`, `Button`, `CellAction`, `CellHeader`, `CellInput`, `CellList`,
`CellSimple`, `Counter`, `IconButton`, `Input`, `MaxUI`, `Spinner`, `Textarea`,
`Typography` and the package's exported icons/hooks.

Use these verified exports and props. Before using a `Typography` component or
an unlisted prop, inspect its installed declaration for the exact signature.
Read the relevant declaration and continue with the requested main screen.

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
public exports in `0.2.0`. Build layout with semantic HTML/CSS. If the requested
journeys need bottom navigation, use accessible buttons/links and `lucide-react`
icons. Avoid new usage of deprecated `Switch` and `ToolButton`.

## Product quality floor

- Start with the requested main screen and primary journey before auxiliary
  files. Navigation is optional and follows those journeys. Do not infer screens,
  a cart, history, profile or a tab count from the industry or project name.
- For new UI or an explicit redesign, choose a concrete visual direction from
  the product's content, audience and use scene. Resolve the first-screen
  composition, main/secondary action relationship, type roles and scale,
  surface hierarchy, spacing rhythm and color roles on axes the owner left open.
  A list of colors or adjectives such as "modern" is not a direction. Choose
  expression that fits the task; operating UI can have a distinct character.
- In the same coding turn compare that direction with the implementation and
  the owner's main journey. Record the actual product choices in `DESIGN.md`,
  retaining the typography metadata. This adds no separate model request,
  visual acceptance gate, screenshot critique or refinement loop. Do not report
  visual verification when the available tools cannot show the rendered result.
- One visually dominant action per screen. Use hierarchy, spacing and type
  scale instead of wrapping every item in the same card.
- Every data surface has useful loading, empty, error and success states.
- Design the first visit as deliberately as the populated state. With no data,
  show the useful next action and a finished composition; do not expand empty
  graphs or duplicate empty cards just to occupy space. Keep explicitly requested
  views usable and never fabricate business records, personal metrics or prices.
- Use concrete copy in the owner's requested language and only supplied or
  explicitly requested seed data. Every visible control
  performs an action or is clearly disabled with an explanation.
- At 360–390px: prevent accidental page-wide horizontal overflow; touch targets are
  at least 44px and safe areas and the keyboard do not cover the active control or CTA.
  A deliberately contained gallery, tab strip or other requested scrolling surface
  is allowed when its boundaries, controls and content remain usable on the phone.
- Prefer one consistent icon style, using `lucide-react` when the owner has not
  requested a different treatment. Avoid arbitrary decorative icons.
- Use the active generation mode's completion checks and repair concrete errors.
