import type { Metadata } from "next";
import { Smartphone } from "lucide-react";
import { getLocale } from "next-intl/server";

import { ProductComingSoon } from "@/components/marketing/ProductComingSoon";

export const metadata: Metadata = {
  title: "Мобильные приложения — в разработке",
  description: "Отдельная студия Omnia для мобильных приложений готовится к запуску.",
};

export default async function AppsPage() {
  const locale = await getLocale();
  const en = locale === "en";
  return (
    <ProductComingSoon
      locale={locale}
      Icon={Smartphone}
      title={en ? "Mobile applications" : "Мобильные приложения"}
      description={
        en
          ? "A separate studio for native-feeling iOS and Android products, stores and release support is in development."
          : "Готовим отдельную студию для приложений iOS и Android, публикации в сторах и сопровождения релизов."
      }
      capabilities={
        en
          ? ["Mobile product brief", "Device capabilities", "Store preparation", "Release and updates"]
          : ["Мобильный продуктовый бриф", "Возможности устройства", "Подготовка сторов", "Релиз и обновления"]
      }
    />
  );
}
