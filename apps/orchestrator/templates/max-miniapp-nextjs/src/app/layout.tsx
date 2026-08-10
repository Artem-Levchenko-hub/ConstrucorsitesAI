import "@maxhub/max-ui/dist/styles.css";
import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./max-runtime.css";
import "./globals.css";

import { MaxAppProvider } from "@/components/MaxAppProvider";
import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata: Metadata = {
  title: app.app_name,
  description: app.summary,
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <Script src="https://st.max.ru/js/max-web-app.js" strategy="beforeInteractive" />
        <MaxAppProvider>{children}</MaxAppProvider>
        <Script src="/omnia-inspector.js" strategy="afterInteractive" />
        <Script src="/omnia-remix-cta.js" strategy="afterInteractive" />
        <Script src="/omnia-brief-narration.js" strategy="afterInteractive" />
      </body>
    </html>
  );
}
