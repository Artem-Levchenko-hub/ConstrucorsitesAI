# MAX headless platform adapter

- The maintained runtime already owns MAX Bridge, verified `initData`, the MAX
  profile/session, bot webhook, legal/support routes and managed integrations.
- It is not a visual or product template. Build the user-visible product in
  `src/components/product/ProductApp.tsx`; the product owns layout, navigation,
  copy, states and styling.
- On a fresh build, write a complete usable `ProductApp.tsx` before extracting
  helpers or changing other product files. Never import `@maxhub/max-ui` in a
  fresh product; that package remains only for historical snapshot compatibility.
  Use ordinary React, Tailwind or product CSS. Run `build` and fix factual errors.
- Do not edit the locked root page/layout, MAX runtime, API routes, package/build
  config or server secrets. Do not create parallel API routes or email/password auth.
- Use `useMaxApp` from `@/components/MaxAppProvider` and managed functions from
  `@/lib/omnia/integration-client` only when the requested product needs them.
- Demo/local data is allowed when requested or useful for preview. Never embed a
  credential or present demo identity as the authenticated MAX user.
- Do not add a MAX visual shell, mandatory legal footer/marker, design spec or
  platform-themed component system. The final browser gate only checks that the
  compiled `ProductApp` hydrates into a real visible screen.
