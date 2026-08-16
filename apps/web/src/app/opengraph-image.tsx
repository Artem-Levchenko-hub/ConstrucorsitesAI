import { ImageResponse } from "next/og";

// Node runtime — Edge requires streaming infra our standalone container lacks.
export const runtime = "nodejs";
export const alt = "Omnia — приложение для MAX без команды разработки";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/**
 * Auto-generated Open Graph card. Satori (next/og's renderer) is strict:
 * every <div> with multiple children must have explicit display: flex|none,
 * and <br/> inside text counts as a child — so we use one <div> per line.
 */
export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          background: "#171716",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          fontFamily: "Inter, system-ui, sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            color: "#f15a38",
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
            display: "flex",
            color: "#fafafa",
            fontSize: 72,
            fontWeight: 700,
            lineHeight: 1.05,
            letterSpacing: "-0.04em",
          }}
        >
          Приложение для MAX
        </div>
        <div
          style={{
            display: "flex",
            color: "#fafafa",
            fontSize: 72,
            fontWeight: 700,
            lineHeight: 1.05,
            letterSpacing: "-0.04em",
          }}
        >
          без команды разработки
        </div>
        <div
          style={{
            display: "flex",
            marginTop: 32,
            color: "#aaa59b",
            fontSize: 28,
            fontWeight: 400,
            maxWidth: 1000,
          }}
        >
          Интерфейс, backend, интеграции, MAX-бот, HTTPS и управляемая публикация.
        </div>
      </div>
    ),
    { ...size },
  );
}
