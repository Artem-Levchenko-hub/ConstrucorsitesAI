import type { Metadata } from "next";
import { PanelsTopLeft } from "lucide-react";
import { getLocale } from "next-intl/server";

import { ProductComingSoon } from "@/components/marketing/ProductComingSoon";

export const metadata: Metadata = {
  title: "Лендинги — в разработке",
  description: "Отдельная студия Omnia для лендингов готовится к запуску.",
};

export default async function LandingsPage() {
  const locale = await getLocale();
  const en = locale === "en";
  return (
    <ProductComingSoon
      locale={locale}
      Icon={PanelsTopLeft}
      title={en ? "Landing pages" : "Лендинги"}
      description={
        en
          ? "A dedicated workflow for marketing pages, content, forms, analytics, domains and publishing is in development."
          : "Разрабатываем отдельный сценарий для маркетинговых страниц, контента, форм, аналитики, доменов и публикации."
      }
      capabilities={
        en
          ? ["Offer and structure", "Content and visual direction", "Forms and analytics", "Domain and release"]
          : ["Оффер и структура", "Контент и визуальное направление", "Формы и аналитика", "Домен и релиз"]
      }
    />
  );
}
