import type { Metadata } from "next";
import Script from "next/script";

import "./globals.css";

export const metadata: Metadata = {
  title: "Omnia Realtime",
  description: "Realtime application built on Omnia.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru" className="dark">
      <head>
        {/* Real typography — a `<link>` (not next/font) so the font loads in the
            browser with no container build-time fetch. Plus Jakarta Sans is the
            default; a generated app can swap the family here + --font-sans. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap"
          rel="stylesheet"
        />
        {/* Ships DARK; honour an explicit localStorage choice if the app adds a
            theme toggle. Runs before paint → no flash. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('theme');document.documentElement.classList.toggle('dark',t?t==='dark':true);}catch(e){document.documentElement.classList.add('dark');}})();",
          }}
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
        {/* Omnia select-mode inspector — canonical copy of apps/api
            static/omnia-inspector.js (drift-guarded). Powers «Править с ИИ»
            (click-to-pick) + the manual style editor over postMessage. Dormant
            until the workspace enables it, so it costs nothing in normal use. */}
        <Script src="/omnia-inspector.js" strategy="afterInteractive" />
        {/* Omnia viral "Remix this" CTA — top-level public viewer only (hidden
            inside the owner-workspace iframe). Drift-synced. */}
        <Script src="/omnia-remix-cta.js" strategy="afterInteractive" />
        {/* Omnia brief-narration — "AI is designing" reveal. Inert without a
            baked window.__omniaBrief; exposes the watermark replay hook. */}
        <Script src="/omnia-brief-narration.js" strategy="afterInteractive" />
      </body>
    </html>
  );
}
