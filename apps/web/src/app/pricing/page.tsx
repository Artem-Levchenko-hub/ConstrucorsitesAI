import { ArrowRight, CreditCard, ReceiptText, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { PublicPageShell } from "@/components/marketing/PublicPageShell";

export default function PricingPage() {
  return (
    <PublicPageShell
      eyebrow="Тарифы"
      title="Актуальные условия — в вашем аккаунте"
      lead="Тарифы, лимиты и суммы оплаты приходят с сервера. Перед оплатой вы увидите состав выбранного плана и итоговую сумму."
    >
      <div className="grid gap-5 lg:grid-cols-3">
        {[
          { Icon: ReceiptText, title: "Данные с сервера", text: "Название, стоимость, включённый кредит и лимиты показываются из действующей конфигурации." },
          { Icon: CreditCard, title: "Проверка до оплаты", text: "Сначала открывается сводка заказа. Переход в ЮKassa выполняется только после подтверждения." },
          { Icon: ShieldCheck, title: "Статус подтверждает сервер", text: "Интерфейс не активирует баланс или тариф локально: результат появляется после проверки платежа." },
        ].map(({ Icon, title, text }) => (
          <article key={title} className="rounded-[12px] border border-border-default bg-surface p-7">
            <Icon className="size-5 text-accent" />
            <h2 className="mt-7 text-lg font-semibold">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-fg-secondary">{text}</p>
          </article>
        ))}
      </div>
      <div className="mt-8 flex flex-col items-start justify-between gap-5 border-t border-border-default pt-8 sm:flex-row sm:items-center">
        <p className="max-w-[650px] text-sm leading-6 text-fg-secondary">Войдите, чтобы открыть действующий тариф и доступные варианты. Если сессии нет, сервис безопасно вернёт вас в этот раздел после входа.</p>
        <Link href="/login?next=/billing/plan" className="max-public-button max-public-button--primary">Посмотреть тарифы в аккаунте <ArrowRight className="size-4" /></Link>
      </div>
    </PublicPageShell>
  );
}
