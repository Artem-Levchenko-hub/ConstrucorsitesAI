import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/max",
    name: "Yleum — ваши приложения",
    short_name: "Yleum",
    description: "Создавайте приложения и управляйте бизнесом с телефона.",
    lang: "ru",
    start_url: "/max",
    scope: "/",
    display: "standalone",
    background_color: "#f5f6fa",
    theme_color: "#f5f6fa",
    icons: [
      { src: "/pwa/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/pwa/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/pwa/icon-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
