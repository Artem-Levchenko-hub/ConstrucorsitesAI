import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";

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
    <html lang="ru">
      <body className="bg-zinc-950 text-zinc-100 antialiased">
        {children}
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
