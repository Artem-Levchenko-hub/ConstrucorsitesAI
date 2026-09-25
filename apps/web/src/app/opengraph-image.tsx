import { ImageResponse } from "next/og";

// Node runtime — Edge requires streaming infra our standalone container lacks.
export const runtime = "nodejs";
export const alt = "Yleum — приложение для MAX без команды разработки";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/**
 * Auto-generated Open Graph card. Satori (next/og's renderer) is strict:
 * every <div> with multiple children must have explicit display: flex|none,
 * and <br/> inside text counts as a child — so we use one <div> per line.
 *
 * Фирменная полоса сверху — градиент знака Yleum: голубой → синий → фиолетовый.
 */
export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          background: "#121519",
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
            position: "absolute",
            top: 0,
            left: 0,
            width: "1200px",
            height: "10px",
            backgroundImage:
              "linear-gradient(90deg, #25c6fb 0%, #0062ee 38%, #2441f7 62%, #804ffa 84%, #c472fa 100%)",
          }}
        />
        <div
          style={{
            display: "flex",
            color: "#0381fa",
            fontSize: 36,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            marginBottom: 24,
          }}
        >
          Yleum
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
            color: "#828491",
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
