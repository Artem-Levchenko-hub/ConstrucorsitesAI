# MAX headless platform adapter

- This workspace is the MAX Studio tab. Build an application that runs inside
  the MAX messenger — never pivot to an ordinary site, Telegram/VK Mini App or
  a separate web product.
- The maintained runtime already owns MAX Bridge, verified `initData`, the MAX
  profile/session, bot webhook, legal/support routes and managed integrations.
- It is not a visual or product template. Build the user-visible product in
  `src/components/product/ProductApp.tsx`; the product owns layout, navigation,
  copy, states and styling.
- On a fresh build, create a complete usable `ProductApp.tsx` and organise any
  supporting product files in the order that best fits the implementation.
  Never import `@maxhub/max-ui` in a fresh product; that package remains only for historical snapshot compatibility.
  Use ordinary React, Tailwind or product CSS. Run `build` and fix factual errors.
- A real project shell is available for diagnostics, package scripts, tests and
  code generation inside the isolated `/app` container. Use normal file tools
  for source edits and declare every path a shell command may mutate so its
  exact bytes are tracked in the generated snapshot.
- Do not edit the locked root page/layout, MAX runtime, API routes, package/build
  config or server secrets. Do not create parallel API routes or email/password auth.
- Use `useMaxApp` from `@/components/MaxAppProvider` and managed functions from
  `@/lib/omnia/integration-client` only when the requested product needs them.
- Demo/local data is allowed when requested or useful for preview. Never embed a
  credential or present demo identity as the authenticated MAX user.
- Derive a restrained visual system from this product's audience, task and
  content. Do not default to a dark-purple AI dashboard, glass/bento cards,
  decorative gradients, huge radii, repeated badges or generic AI/premium copy.
  Never use emoji as interface icons; use one coherent SVG/icon set.
- Every chart, metric, card and status must communicate useful real or clearly
  labelled demo data. Do not add empty decorative charts or repeat one generic
  card pattern across every screen.
- Implement and verify every requested destination and primary action. Repeated
  ordinary tab clicks must stay responsive when editor mode is off.
- Do not add a MAX visual shell, mandatory legal footer/marker, design spec or
  platform-themed component system. The final browser gate only checks that the
  compiled `ProductApp` hydrates into a real visible screen.
