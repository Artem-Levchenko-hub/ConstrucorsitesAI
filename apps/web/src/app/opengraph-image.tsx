import { ImageResponse } from "next/og";

// Node runtime — Edge runtime requires cloudflare/vercel-style streaming that
// our standalone-output Docker image doesn't ship. Node works everywhere.
export const runtime = "nodejs";
export const alt = "Omnia.AI — пиши промпты, получай готовый сайт";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/**
 * Auto-generated Open Graph image. Replaces a static og-image.png so we don't
 * need to ship a binary asset before the brand is finalized.
 */
export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          background: "linear-gradient(135deg, #0a0a0a 0%, #1e293b 100%)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "flex-start",
          padding: "80px",
          fontFamily: "Inter, system-ui, sans-serif",
        }}
      >
        <div
          style={{
            color: "#3b82f6",
            fontSize: 36,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            marginBottom: 24,
          }}
        >
          Omnia.AI
        </div>
        <div
          style={{
            color: "#fafafa",
            fontSize: 72,
            fontWeight: 700,
            lineHeight: 1.05,
            letterSpacing: "-0.04em",
            maxWidth: 1000,
          }}
        >
          Пиши промпты —
          <br />
          получай готовый сайт.
        </div>
        <div
          style={{
            marginTop: 32,
            color: "#94a3b8",
            fontSize: 28,
            fontWeight: 400,
            maxWidth: 900,
          }}
        >
          Сайт + бэкенд + домен + хостинг. С историей и кнопкой откатить любой
          промпт. В рублях.
        </div>
      </div>
    ),
    { ...size },
  );
}
