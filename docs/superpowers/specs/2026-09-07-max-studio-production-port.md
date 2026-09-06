# MAX Studio — remaining production frontend

## Approved direction

Port the approved interactive prototype at `/Users/romanisakin/Documents/Codex/2026-08-30/new-chat-2/outputs/max-studio-cjm` into the existing production Next.js frontend. The user has explicitly requested all remaining screens after the editor release. This is a port, not a new design exploration. Editor release `403e0c0f` is already deployed; baseline source is `7e3af9d5`.

## Design and journey

- MAX Studio only in the public and MAX journeys. Remove ordinary builder/website/product-switch marketing from these surfaces, without deleting unrelated legacy backend or workspace routes.
- Public landing explains idea → MAX application with a labelled illustrative example, then authentication, projects, guided creation, editor, launch, management.
- Use the approved light interface, white surfaces, dark readable text, restrained blue primary actions and semantic green/amber/red statuses. Avoid nested same-size rounded cards and blanket grey typography. Use compact rows, headings, whitespace and dividers to distinguish hierarchy.
- Projects have no redundant one-item sidebar or generic MAX APP cover. Keep useful search, names, server state, and one appropriate next action.
- New project creation is a back/next questionnaire with retained answers and a final review; no project is created before explicit final submission.
- Project settings retain all real fields and integrations. Launch is accessible from the upper toolbar; readiness and optional integrations/server settings are discoverable. Publication state and success must match the current version, not an old successful deploy.
- Management has a clear status/action area, compact operational detail and publication history. No invented visitors, health or published state.
- Account has six distinct sections: profile, organization, security, balance, operations, plan. Clear navigation on mobile, compact destructive confirmation, server-derived prices/status and proper payment review → YooKassa redirect → server-confirmed outcome.
- Top-right action notifications have semantic icons, dismiss controls and restrained entrance/exit animation. Errors must remain dismissible until acknowledged; reduced-motion is respected. Notifications do not claim background work not represented by actual async requests.

## Binding production constraints

Preserve real authentication, API contracts, authorization, generation, preview, version restore, deployment, credentials, payment and subscription logic. Never copy prototype simulated mutations, fake analytics, hardcoded payment prices or local success flags into production. Do not initiate payments, generation, restore or app publication on the user's existing projects during QA. Existing production web deployment is authorized; deploy only the web service through the documented `full` compose stack after verification. Preserve unrelated files and server changes, never force-push.

## Verification

Behavior tests for new wizard/search/auth/payment and state handling; TypeScript, ESLint, full unit suite and production build. Browser review at mobile and desktop widths, anonymous landing/auth, and signed-in read-only MAX/account routes. Record revision, health and remaining functional QA limits without implying a real payment was tested.
