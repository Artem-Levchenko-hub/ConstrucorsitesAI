import { getRequestConfig } from "next-intl/server";
import { cookies } from "next/headers";

import { LOCALES, DEFAULT_LOCALE, type AppLocale } from "@/i18n/config";

export default getRequestConfig(async ({ requestLocale }) => {
  // requestLocale is the locale from the [locale] segment (undefined for cookie-based setup)
  // We ignore it and read directly from the NEXT_LOCALE cookie.
  await requestLocale; // consume the promise to satisfy the API contract
  const store = await cookies();
  const cookieLocale = store.get("NEXT_LOCALE")?.value;
  const locale: AppLocale = (LOCALES as readonly string[]).includes(
    cookieLocale ?? "",
  )
    ? (cookieLocale as AppLocale)
    : DEFAULT_LOCALE;
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
