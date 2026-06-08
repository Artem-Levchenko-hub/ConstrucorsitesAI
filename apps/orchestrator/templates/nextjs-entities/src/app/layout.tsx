import type { Metadata } from "next";
import { Manrope, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";

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

export const metadata: Metadata = {
  title: "Omnia project",
  description: "Made with Omnia.AI",
};

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
      </body>
    </html>
  );
}
