import { CheckCircle2 } from "lucide-react";
import Link from "next/link";

import { PublicPageShell } from "@/components/marketing/PublicPageShell";

const plans = [
  { name: "Старт", price: "0 ₽", text: "Проверить идею и собрать первое приложение.", items: ["1 MAX Mini App", "Первая AI-сборка", "Мобильное превью", "Базовая поддержка"] },
  { name: "Studio", price: "По использованию", text: "Публикация, интеграции и регулярные изменения.", items: ["Активные приложения", "Продуктовый AI-агент", "Платежи и CRM", "HTTPS и webhook", "Приоритетная поддержка"], popular: true },
  { name: "Enterprise", price: "Индивидуально", text: "Для команд и корпоративной инфраструктуры.", items: ["Собственная VPS", "Корпоративные API", "Контроль доступности", "Персональное сопровождение"] },
];

export default function PricingPage() {
  return (
    <PublicPageShell
      eyebrow="Тарифы"
      title="Стоимость соответствует реальному использованию"
      lead="Бесплатно проверьте сценарий, затем оплачивайте генерации и эксплуатацию без скрытой команды разработки."
    >
      <div className="grid gap-6 lg:grid-cols-3">
        {plans.map((plan) => (
          <article key={plan.name} className={`relative rounded-2xl border bg-[#13172a] p-8 ${plan.popular ? "border-[#3b82f6]" : "border-[#202946]"}`}>
            {plan.popular && <span className="absolute right-8 top-8 rounded-full border border-[#244a85] bg-[#10213f] px-3 py-1 text-[10px] uppercase text-[#3b82f6]">Популярный</span>}
            <h2 className="text-[18px] font-semibold">{plan.name}</h2>
            <p className="mt-3 min-h-12 text-[14px] leading-6 text-[#94a3b8]">{plan.text}</p>
            <div className="mt-7 text-[32px] font-bold tracking-[-0.04em]">{plan.price}</div>
            <ul className="mt-7 space-y-4">
              {plan.items.map((item) => <li key={item} className="flex items-center gap-2.5 text-[13px] text-[#94a3b8]"><CheckCircle2 className="h-4 w-4 text-[#3b82f6]" />{item}</li>)}
            </ul>
            <Link href="/max/register" className={`mt-8 inline-flex h-11 w-full items-center justify-center rounded-lg text-[13px] font-semibold ${plan.popular ? "bg-gradient-to-r from-[#3b82f6] to-[#8b5cf6]" : "border border-[#202946]"}`}>
              Начать
            </Link>
          </article>
        ))}
      </div>
    </PublicPageShell>
  );
}
