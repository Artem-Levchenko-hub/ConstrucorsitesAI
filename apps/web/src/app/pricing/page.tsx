import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { PublicPageShell } from "@/components/marketing/PublicPageShell";
import { getSession } from "@/lib/auth-mock";
import { getPublicBillingPlans } from "@/lib/public-billing-plans";
import { planAllowanceRows, planPeriodDescription } from "@/lib/billing-allowances";

const money = (value: string) => new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(Number(value));
const limit = (value: unknown) => value === null ? "Без ограничений" : value === undefined ? "—" : String(value);

export default async function PricingPage() {
  const [session, plans] = await Promise.all([getSession(), getPublicBillingPlans()]);
  const pricingHref = session ? "/billing/plan" : "/login?next=/billing/plan";

  return (
    <PublicPageShell
      eyebrow="Тарифы"
      title="Выберите тариф для своего приложения"
      lead="Актуальная стоимость и возможности доступны без регистрации. Перед оплатой вы увидите выбранный тариф и итоговую сумму."
    >
      {plans ? <div className="grid gap-5 lg:grid-cols-3">
        {plans.map(plan => <article key={plan.id} className="min-w-0 rounded-[12px] border border-border-default bg-surface p-7">
          <h2 className="text-xl font-semibold">{plan.name}</h2>
          <p className="mt-5 text-3xl font-semibold">{money(plan.price_rub)}<span className="ml-1 text-sm font-normal text-fg-secondary">/ месяц</span></p>
          <dl className="mt-6 space-y-3 text-sm">
            {([["max_projects", "Проектов"], ["static_publish_slots", "Публикаций"], ["team_seats", "Мест в команде"], ["always_on_slots", "Постоянно работающих приложений"]] as const).map(([key, label]) => plan.entitlements[key] !== undefined && <div key={key} className="flex items-start justify-between gap-4">
              <dt className="text-fg-secondary">{label}</dt><dd className="text-right font-medium">{limit(plan.entitlements[key])}</dd>
            </div>)}
            {planAllowanceRows(plan).map(row => <div key={row.key} className="flex items-start justify-between gap-4"><dt className="text-fg-secondary">{row.label}</dt><dd className="text-right font-medium">{row.value}</dd></div>)}
            {typeof plan.entitlements.integrations === "boolean" && <div className="flex justify-between gap-4"><dt className="text-fg-secondary">Интеграции</dt><dd>{plan.entitlements.integrations ? "Да" : "Нет"}</dd></div>}
          </dl>
          <p className="mt-6 text-sm leading-6 text-fg-secondary">{planPeriodDescription(plan)}</p>
          <Link href={pricingHref} className="max-public-button max-public-button--primary mt-6">{plan.code === "free" ? "Начать бесплатно" : `Выбрать ${plan.name}`} <ArrowRight className="size-4 shrink-0" /></Link>
        </article>)}
      </div> : <div role="status" className="rounded-[12px] border border-border-default bg-surface p-7">
        <h2 className="text-lg font-semibold">Не удалось загрузить тарифы</h2>
        <p className="mt-2 text-sm leading-6 text-fg-secondary">Актуальные цены временно недоступны. Обновите страницу или откройте раздел тарифов в аккаунте.</p>
        <Link href={pricingHref} className="max-public-button max-public-button--primary mt-6">Открыть тарифы в аккаунте <ArrowRight className="size-4" /></Link>
      </div>}
      <div className="mt-8 border-t border-border-default pt-8 text-sm leading-6 text-fg-secondary">
        <p>Автопродление подключается только с отдельного согласия при оплате. Текущий тариф и условия действующей подписки можно проверить в аккаунте.</p>
        <p className="mt-2">Интеграция с amoCRM доступна без отдельной платы со стороны Yleum. Лицензия amoCRM оплачивается отдельно по тарифам amoCRM.</p>
      </div>
    </PublicPageShell>
  );
}
