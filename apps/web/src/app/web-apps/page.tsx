import type { Metadata } from "next";
import { Globe } from "lucide-react";
import { getLocale } from "next-intl/server";

import { ProductComingSoon } from "@/components/marketing/ProductComingSoon";

export const metadata: Metadata = {
  title: "Веб-приложения — в разработке",
  description: "Отдельная студия Omnia для веб-приложений готовится к запуску.",
};

export default async function WebAppsPage() {
  const locale = await getLocale();
  const en = locale === "en";
  return (
    <ProductComingSoon
      locale={locale}
      Icon={Globe}
      title={en ? "Web applications" : "Веб-приложения"}
      description={
        en
          ? "A focused studio for portals, CRM systems, catalogues and database products is being prepared."
          : "Готовим отдельную среду для личных кабинетов, CRM, каталогов и продуктов с базой данных."
      }
      capabilities={
        en
          ? ["Data model and roles", "Business workflows", "Testing and deployment", "Operations and integrations"]
          : ["Модель данных и роли", "Бизнес-сценарии", "Тесты и публикация", "Эксплуатация и интеграции"]
      }
    />
  );
}
