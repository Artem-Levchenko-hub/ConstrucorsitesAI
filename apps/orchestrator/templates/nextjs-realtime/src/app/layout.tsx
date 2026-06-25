import type { Metadata } from "next";

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
    <html lang="ru">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
