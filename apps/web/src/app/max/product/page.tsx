import type { Metadata } from "next";

import { YleumLanding } from "@/components/marketing/YleumLanding";

export const metadata: Metadata = {
  title: "Yleum — создайте приложение для бизнеса без кода",
  description: "Каталог, запись на услуги или клуб клиентов внутри MAX. Опишите идею, соберите приложение с ИИ и меняйте его в чате. Начните на бесплатном тарифе Free.",
  alternates: { canonical: "/max/product" },
};

export default function MaxProductPage() {
  return <YleumLanding />;
}
