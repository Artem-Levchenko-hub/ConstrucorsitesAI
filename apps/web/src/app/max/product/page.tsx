import type { Metadata } from "next";

import { YleumLanding } from "@/components/marketing/YleumLanding";
import { getPublicBillingPlans } from "@/lib/public-billing-plans";

export const metadata: Metadata = {
  title: "Yleum — создайте приложение для бизнеса без кода",
  description: "Каталог, запись на услуги или клуб клиентов внутри MAX. Опишите идею, соберите приложение с ИИ и меняйте его в чате. Начните на бесплатном тарифе.",
  alternates: { canonical: "/max/product" },
};

export default async function MaxProductPage() {
  return <YleumLanding plans={await getPublicBillingPlans()} />;
}
