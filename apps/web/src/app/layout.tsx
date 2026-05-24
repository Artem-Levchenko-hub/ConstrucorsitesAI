import type { Metadata, Viewport } from "next";
import { Onest, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const onest = Onest({
  subsets: ["latin", "cyrillic"],
  variable: "--font-onest",
  weight: ["300", "400", "500", "600", "700", "800"],
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
const SITE_TITLE = "Omnia.AI — AI-сайт-билдер: пиши промпты, получай готовый сайт";
const SITE_DESC =
  "Опиши сайт словами — Omnia.AI сгенерирует страницы, бэкенд, домен и хостинг. " +
  "С историей версий и кнопкой «откатить» на любой промпт. Оплата в рублях, без подписок.";

export const metadata: Metadata = {
  metadataBase: new URL(PUBLIC_ORIGIN),
  title: {
    default: SITE_TITLE,
    template: "%s · Omnia.AI",
  },
  description: SITE_DESC,
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
    title: SITE_TITLE,
    description: SITE_DESC,
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
    title: SITE_TITLE,
    description: SITE_DESC,
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

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0d0d12" },
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
  description: SITE_DESC,
  inLanguage: "ru-RU",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="ru"
      className={`dark ${onest.variable} ${jetbrainsMono.variable}`}
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
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
