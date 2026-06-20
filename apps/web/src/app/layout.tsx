import type { Metadata, Viewport } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import "./globals.css";
import { Providers } from "./providers";

const interTight = Inter_Tight({
  subsets: ["latin", "cyrillic"],
  variable: "--font-inter-tight",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "cyrillic"],
  variable: "--font-jetbrains",
  weight: ["400", "500", "600"],
  display: "swap",
});

const PUBLIC_ORIGIN =
  process.env.NEXT_PUBLIC_API_URL ?? "https://constructor.lead-generator.ru";

const SITE_NAME = "Omnia.AI";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");

  const title = t("title");
  const description = t("description");

  return {
    metadataBase: new URL(PUBLIC_ORIGIN),
    title: {
      default: title,
      template: "%s · Omnia.AI",
    },
    description,
    applicationName: SITE_NAME,
    authors: [{ name: "Omnia.AI" }],
    generator: "Omnia.AI",
    keywords: [
      "AI сайт-билдер",
      "конструктор сайтов",
      "сайт под ключ",
      "AI генератор сайтов",
      "лендинг по промпту",
      "сайт на русском",
      "GigaChat сайт",
      "no-code сайт",
      "сделать сайт без программиста",
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
      url: PUBLIC_ORIGIN,
      images: [
        {
          url: "/og-image.png",
          width: 1200,
          height: 630,
          alt: "Omnia.AI — AI-сайт-билдер",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og-image.png"],
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
    icons: {
      icon: [
        { url: "/favicon.ico", sizes: "any" },
        { url: "/icon.svg", type: "image/svg+xml" },
      ],
      apple: "/apple-icon.png",
    },
    category: "technology",
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
  width: "device-width",
  initialScale: 1,
};

const ORG_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "Organization",
  name: SITE_NAME,
  url: PUBLIC_ORIGIN,
  logo: `${PUBLIC_ORIGIN}/icon.svg`,
  sameAs: [
    // Fill in as accounts are created
    // "https://t.me/omnia_ai",
    // "https://vk.com/omnia_ai",
  ],
};

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
  description: "AI-сайт-билдер для российского рынка",
  inLanguage: "ru-RU",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html
      lang={locale}
      className={`dark ${interTight.variable} ${jetbrainsMono.variable}`}
    >
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(ORG_JSON_LD) }}
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
