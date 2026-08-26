import { CheckCircle2 } from "lucide-react";
import Link from "next/link";

import { PublicPageShell } from "@/components/marketing/PublicPageShell";

const plans = [
  {
    name: "Стартовый пакет",
    price: "490 ₽",
    credit: "500 ₽ на баланс",
    text: "Для первой рабочей сборки и небольших изменений.",
    items: ["AI-сборка сайта или приложения", "Мобильное превью", "Редактирование через агента", "Публикация при готовности"],
  },
  {
    name: "Бизнес-пакет",
    price: "1 490 ₽",
    credit: "1 600 ₽ на баланс",
    text: "Для полноценного запуска с интеграциями и итерациями.",
    items: ["Создание и доработка продукта", "Платежи, CRM и внешние сервисы", "HTTPS и webhook", "Приоритетная поддержка"],
    popular: true,
  },
  {
    name: "Профессиональный пакет",
    price: "3 990 ₽",
    credit: "4 500 ₽ на баланс",
    text: "Для нескольких проектов или регулярного развития.",
    items: ["Все возможности конструктора", "Большой запас генераций", "Публикация рабочих версий", "Поддержка сложных запусков"],
  },
];

export default function PricingPage() {
  return (
    <PublicPageShell
      eyebrow="Тарифы"
      title="Разовые пакеты для создания и развития продукта"
      lead="Это не подписка. После оплаты сумма зачисляется на баланс и расходуется только на выбранные генерации и внешние услуги."
    >
      <div className="grid gap-6 lg:grid-cols-3">
        {plans.map((plan) => (
          <article key={plan.name} className={`relative rounded-[12px] border bg-[#191b20] p-8 ${plan.popular ? "border-2 border-[#4f81f7]" : "border-[#2b2d32]"}`}>
            {plan.popular && <span className="absolute right-8 top-8 rounded-full bg-[#4f81f7]/10 px-3 py-1 text-[10px] uppercase text-[#6a95fa]">Популярный</span>}
            <h2 className="text-[18px] font-semibold">{plan.name}</h2>
            <p className="mt-3 min-h-12 text-[14px] leading-6 text-[#9fa1b1]">{plan.text}</p>
            <div className="mt-7 text-[32px] font-bold tracking-[-0.04em]">{plan.price}</div>
            <p className="mt-1 text-xs font-semibold text-success-fg">{plan.credit}</p>
            <ul className="mt-7 space-y-4">
              {plan.items.map((item) => <li key={item} className="flex items-center gap-2.5 text-[13px] text-[#9fa1b1]"><CheckCircle2 className="h-4 w-4 text-success-fg" />{item}</li>)}
            </ul>
            <Link href="/max/register" className={`mt-8 inline-flex h-11 w-full items-center justify-center rounded-[8px] text-[13px] font-semibold ${plan.popular ? "bg-[#4f81f7] text-[#121519]" : "border border-[#2b2d32]"}`}>
              Создать аккаунт
            </Link>
          </article>
        ))}
      </div>
      <div className="mt-8 rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-6 text-sm leading-6 text-[#9fa1b1]">
        Итоговая стоимость каждой операции показывается в интерфейсе до запуска.
        Баланс активируется после подтверждения платежа ЮKassa. Порядок оказания
        услуг и возврата описан в{" "}
        <Link className="font-semibold text-[#6a95fa]" href="/legal/offer">
          публичной оферте
        </Link>
        .
      </div>
    </PublicPageShell>
  );
}
