import type { Metadata, Viewport } from "next";
import { Geist, Inter, JetBrains_Mono, Onest, Outfit } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import "./globals.css";
import { publicOrigin } from "@/lib/public-origin";
import { Providers } from "./providers";

const inter = Inter({
  subsets: ["latin", "cyrillic"],
  variable: "--font-inter",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

// Заголовочный шрифт лендинга: плотная геометрика с полноценной кириллицей.
// Geist кириллицу не покрывает, поэтому на витрине он не используется.
const onest = Onest({
  subsets: ["latin", "cyrillic"],
  variable: "--font-onest",
  weight: ["500", "600", "700"],
  display: "swap",
});

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-outfit",
  weight: ["500", "600", "700"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "cyrillic"],
  variable: "--font-jetbrains",
  weight: ["400", "500", "600"],
  display: "swap",
});

const SITE_NAME = "Yleum";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");

  const title = t("title");
  const description = t("description");
  // Per request, never module scope: the domain belongs to the running
  // container, not to the image (see lib/public-origin.ts).
  const origin = publicOrigin();

  return {
    metadataBase: new URL(origin),
    title: {
      default: title,
      template: "%s · Yleum",
    },
    description,
    applicationName: SITE_NAME,
    authors: [{ name: "Yleum" }],
    generator: "Yleum",
    keywords: [
      "MAX Mini App",
      "конструктор MAX",
      "бот MAX",
      "мини-приложение MAX",
      "разработка цифровых продуктов",
      "Yleum",
    ],
    alternates: {
      canonical: "/",
      languages: {
        "ru-RU": "/",
        "x-default": "/",
      },
    },
    openGraph: {
      type: "website",
      locale: "ru_RU",
      siteName: SITE_NAME,
      title,
      description,
      url: origin,
      images: [
        {
          url: "/og.png",
          width: 1200,
          height: 630,
          alt: "Yleum — приложения для бизнеса внутри MAX",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og.png"],
    },
    robots: {
      index: true,
      follow: true,
      googleBot: {
        index: true,
        follow: true,
        "max-image-preview": "large",
        "max-snippet": -1,
      },
    },
    // verification stubs — fill after verifying domain in respective consoles
    verification: {
      google: process.env.NEXT_PUBLIC_GOOGLE_VERIFICATION,
      yandex: process.env.NEXT_PUBLIC_YANDEX_VERIFICATION,
      other: {
        "yandex-verification": process.env.NEXT_PUBLIC_YANDEX_VERIFICATION ?? "",
      },
    },
    // Иконки не перечисляем вручную: Next сам подставит app/icon.svg и
    // app/apple-icon.tsx. Раньше здесь был жёсткий /favicon.ico, которого нет
    // в репозитории — каждая страница тянула 404.
    category: "technology",
  };
}

export const viewport: Viewport = {
  themeColor: "#121519",
  width: "device-width",
  initialScale: 1,
};

/** Built per request for the same reason as the metadata: the origin is a run-time value. */
function organizationJsonLd(origin: string) {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: SITE_NAME,
    url: origin,
    logo: `${origin}/icon.svg`,
    sameAs: [
      // Fill in as accounts are created
      // "https://t.me/omnia_ai",
      // "https://vk.com/omnia_ai",
    ],
  };
}

const APP_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: SITE_NAME,
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "RUB",
    description: "Бесплатный старт; оплата по факту использования AI-токенов",
  },
  description: "Продуктовая студия для MAX Mini Apps и цифровых сервисов",
  inLanguage: "ru-RU",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await getLocale();
  const messages = await getMessages();
  const orgJsonLd = organizationJsonLd(publicOrigin());

  return (
    <html
      lang={locale}
      className={`${inter.variable} ${geist.variable} ${outfit.variable} ${onest.variable} ${jetbrainsMono.variable}`}
    >
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(orgJsonLd) }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(APP_JSON_LD) }}
        />
      </head>
      <body className="text-fg-primary font-sans antialiased">
        <NextIntlClientProvider messages={messages}>
          <Providers>{children}</Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
