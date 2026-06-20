// Client-safe i18n constants. NO server-only imports (next/headers) here, so this
// can be imported from both Client Components (LocaleSwitcher) and the server-only
// request config without dragging `next/headers` into the client bundle.
export const LOCALES = ["ru", "en"] as const;
export type AppLocale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: AppLocale = "ru";
