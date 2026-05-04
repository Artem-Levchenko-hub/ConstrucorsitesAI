import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({
  subsets: ["latin", "cyrillic"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "cyrillic"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Omnia.AI — пиши промпты, получай готовый сайт",
  description:
    "AI-сайт-билдер: промпт → сайт с backend, доменом, деплоем и кнопкой «вернуться назад» для каждого промпта. Всё в рублях.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="ru"
      className={`dark ${inter.variable} ${jetbrainsMono.variable}`}
    >
      <body className="bg-surface-base text-fg-primary font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
