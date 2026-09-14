import type { Metadata } from "next";

import { MaxPublicLanding } from "@/components/marketing/MaxPublicLanding";

export const metadata: Metadata = {
  title: "MAX Studio — создайте приложение для бизнеса без кода",
  description: "Каталог, запись на услуги или клуб клиентов внутри MAX. Опишите идею, соберите приложение с ИИ и меняйте его в чате. Начните на бесплатном тарифе Free.",
};

export default function HomePage() {
  return <MaxPublicLanding />;
}
