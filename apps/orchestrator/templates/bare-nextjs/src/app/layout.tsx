import type { Metadata } from "next";
import "./globals.css";

// Bare root layout. The agent may rewrite this entirely (fonts, theme, shell).
export const metadata: Metadata = {
  title: "App",
  description: "Built from scratch by Omnia",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
