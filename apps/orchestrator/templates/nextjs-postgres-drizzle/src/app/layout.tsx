import type { Metadata } from "next";
import "./globals.css";

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
      <body className="bg-zinc-950 text-zinc-100 antialiased">{children}</body>
    </html>
  );
}
