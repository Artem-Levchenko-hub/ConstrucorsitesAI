import { ImageResponse } from "next/og";

import { share } from "./omnia-share";

/* The branded social card a colleague sees the instant a /p/<slug> link is
 * pasted into Telegram / Slack / Discord — the first frame of the viral loop
 * (North Star pillar 4). It is the public twin of <StorefrontHero>: the project
 * title set large on a brand-accent glow, the niche as a quiet kicker, and an
 * Omnia wordmark. Every colour comes from the per-project `share.accent`, so the
 * same route renders a different brand per niche with zero per-app code. */

export const runtime = "nodejs";
export const alt = share.title;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/* Satori (next/og) ships only a Latin default font, so Cyrillic would render as
 * tofu boxes. Pull a Cyrillic-capable Manrope at render time (cards are cached
 * by crawlers, so this is rare) and degrade gracefully to the default font if
 * the network hiccups — a slightly-off card beats a 500. */
async function cyrillicFont(): Promise<ArrayBuffer | null> {
  try {
    const css = await fetch(
      "https://fonts.googleapis.com/css2?family=Manrope:wght@800&display=swap",
      { headers: { "User-Agent": "Mozilla/5.0" } },
    ).then((r) => r.text());
    const url = css.match(/src:\s*url\(([^)]+)\)\s*format\(['"]?(?:woff2|truetype|opentype)/)?.[1];
    if (!url) return null;
    return await fetch(url).then((r) => r.arrayBuffer());
  } catch {
    return null;
  }
}

/* Satori parses rgb()/rgba() and 3-/6-digit hex, but NOT 8-digit #rrggbbaa, so
 * build the glow tints as rgba() from the project accent. */
function rgba(hex: string, alpha: number): string {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return `rgba(99, 102, 241, ${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export default async function Image() {
  const accent = /^#[0-9a-fA-F]{6}$/.test(share.accent) ? share.accent : "#6366f1";
  const font = await cyrillicFont();

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "84px 96px",
          background: "#0b0b0f",
          backgroundImage: `radial-gradient(900px 520px at 78% -8%, ${rgba(accent, 0.36)}, transparent 60%), radial-gradient(700px 480px at -6% 112%, ${rgba(accent, 0.16)}, transparent 55%)`,
          color: "#ffffff",
          fontFamily: "Manrope, sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 9,
              background: accent,
              display: "flex",
            }}
          />
          <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: -0.5 }}>Omnia</div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
          {share.tagline ? (
            <div
              style={{
                display: "flex",
                fontSize: 30,
                fontWeight: 800,
                textTransform: "uppercase",
                letterSpacing: 3,
                color: accent,
              }}
            >
              {share.tagline}
            </div>
          ) : null}
          <div
            style={{
              display: "flex",
              fontSize: share.title.length > 34 ? 80 : 104,
              fontWeight: 800,
              lineHeight: 1.04,
              letterSpacing: -2,
              maxWidth: 1000,
            }}
          >
            {share.title}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 26, color: "#a1a1aa" }}>
          <div style={{ width: 44, height: 3, borderRadius: 2, background: accent, display: "flex" }} />
          Создано на Omnia.AI
        </div>
      </div>
    ),
    {
      ...size,
      fonts: font
        ? [{ name: "Manrope", data: font, weight: 800 as const, style: "normal" as const }]
        : [],
    },
  );
}
