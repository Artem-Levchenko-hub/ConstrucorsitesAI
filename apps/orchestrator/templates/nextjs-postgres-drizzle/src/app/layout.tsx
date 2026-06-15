import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { brief as omniaBrief } from "./omnia-brief";

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
      <head>
        {/* No-flash theme init — flips <html class="dark"> from an explicit
            localStorage choice (the cabinet topbar toggle writes it) else the
            OS prefers-color-scheme, synchronously before first paint. The kit is
            token-driven, so this re-themes the whole cabinet with zero flash.
            The public landing forces its own `.dark` wrapper regardless. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.classList.toggle('dark',d);}catch(e){}})();",
          }}
        />
      </head>
      <body className="bg-background text-foreground antialiased">
        {children}
        {/* Omnia select-mode inspector — synced copy of apps/api static/omnia-inspector.js
            (a drift test keeps them identical). Dormant until the workspace enables it
            over postMessage, so it costs nothing in normal preview/prod use. */}
        <Script src="/omnia-inspector.js" strategy="afterInteractive" />
        {/* Omnia viral "Remix this" CTA — shown only to a top-level public
            viewer (hidden inside the owner-workspace iframe); forks the app into
            a stranger's own editable copy with zero signup. Drift-synced. */}
        <Script src="/omnia-remix-cta.js" strategy="afterInteractive" />
        {/* Per-project art-director brief baked at build time → window.__omniaBrief,
            so the reveal below plays on the SHARED public surface (a stranger
            opening the live or forked app), not only inside the workspace iframe.
            Set synchronously here, before the afterInteractive narration script
            runs. `<` escaped so a section name / motion can't break the tag.
            Null on the un-generated template → nothing rendered, reveal inert. */}
        {omniaBrief ? (
          <script
            id="omnia-brief-data"
            dangerouslySetInnerHTML={{
              __html: `window.__omniaBrief=${JSON.stringify(omniaBrief).replace(
                /</g,
                "\\u003c",
              )};`,
            }}
          />
        ) : null}
        {/* Omnia brief-narration — turns the art-director brief (forwarded by
            the workspace over postMessage, or baked onto window.__omniaBrief)
            into a short "AI is designing" reveal so every generated surface is
            born vocal, not silent. Inert without a brief. Drift-synced. */}
        <Script src="/omnia-brief-narration.js" strategy="afterInteractive" />
      </body>
    </html>
  );
}
