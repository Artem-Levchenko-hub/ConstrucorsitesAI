import type { Metadata } from "next";
import { Manrope, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { share } from "./omnia-share";

/* Default type system. Manrope = clean geometric-humanist UI sans with full
 * Cyrillic; JetBrains Mono = data/figures. The art-director may swap these per
 * brand by editing this file (set the next/font import + the variable). */
const sans = Manrope({
  subsets: ["latin", "cyrillic"],
  variable: "--font-sans",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin", "cyrillic"],
  variable: "--font-mono",
  display: "swap",
});

/* Per-project <head>. Title / description / share-card come from the generated
 * `omnia-share.ts` (services/share_meta.py) so every shared /p/<slug> link
 * unfurls as a branded card — the project's real name + niche over a brand-accent
 * og:image — instead of a generic «Omnia project». `metadataBase` (the public
 * origin) makes the auto-wired opengraph-image URL absolute for crawlers. */
export function generateMetadata(): Metadata {
  const description = share.tagline
    ? `${share.title} — ${share.tagline}. Создано на Omnia.AI`
    : `${share.title}. Создано на Omnia.AI`;
  const origin = process.env.AUTH_URL;
  return {
    metadataBase: origin ? new URL(origin) : undefined,
    title: share.title,
    description,
    openGraph: {
      title: share.title,
      description,
      type: "website",
      siteName: share.title,
    },
    twitter: {
      card: "summary_large_image",
      title: share.title,
      description,
    },
  };
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru" suppressHydrationWarning className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-background font-sans text-foreground antialiased">
        {children}
        <Toaster />
        {/* Omnia select-mode inspector — synced copy of apps/api static/omnia-inspector.js
            (a drift test keeps them identical). Dormant until the workspace enables it
            over postMessage, so it costs nothing in normal preview/prod use. */}
        <Script src="/omnia-inspector.js" strategy="afterInteractive" />
        {/* Omnia viral "Remix this" CTA — shown only to a top-level public
            viewer (hidden inside the owner-workspace iframe); forks the app into
            a stranger's own editable copy with zero signup. Drift-synced. */}
        <Script src="/omnia-remix-cta.js" strategy="afterInteractive" />
      </body>
    </html>
  );
}
