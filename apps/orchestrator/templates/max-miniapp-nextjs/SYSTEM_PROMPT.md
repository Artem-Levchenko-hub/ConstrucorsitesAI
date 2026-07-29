# MAX Mini App generation contract

- Keep React at 18.3.1 because the official MAX UI peer contract requires it.
- Use `window.WebApp` only through `src/lib/max/bridge.ts`.
- Never trust `initDataUnsafe` for authorization. Server data access starts
  after `/api/max/session` validates `initData`.
- Keep bot credentials and webhook secrets server-only.
- Every user-owned table stores `maxUserId`; every read and mutation filters by
  the verified session user.
- Preserve webhook secret verification, request-size limits and event
  idempotency.
- Use MAX UI controls, theme, safe-area padding and mobile touch targets.
- Use BackButton for nested views and closing confirmation only while data is
  unsaved.
- Request contacts or other sensitive platform data only after an explicit
  user action.
- Do not add Telegram WebApp, VK Bridge, Auth.js or password login.
