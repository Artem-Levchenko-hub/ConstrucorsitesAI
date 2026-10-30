"use client";
import { Button } from "@/components/ui/button";
import { packageAllowanceDescription } from "@/lib/billing-allowances";
import { AccountBalance } from "./AccountBalance";
import { AccountUsage } from "./AccountUsage";
import type { PaymentJourney } from "./PaymentCheckout";
import { money, QueryError } from "./account-presentation";
import "./account-billing.css";

export function AccountBilling({ journey }: { journey: PaymentJourney }) {
  const { config, locked, choose } = journey;
  const packagesDisabled = !config.data?.enabled || locked;

  return <div className="account-billing billing-overview">
    <AccountBalance />
    <section className="account-panel account-packages" id="billing-packages" aria-label="Пакеты правок">
      <header className="account-packages-heading">
        <h2>Пополнить баланс</h2>
        <p className="account-hint">Выберите пакет, затем проверьте заказ перед оплатой.</p>
      </header>
      <QueryError error={config.error} retry={() => void config.refetch()} />
      {config.isPending && <p role="status">Загружаем пакеты…</p>}
      <div className="account-package-list">
        {config.data?.packages.map(item => {
          const allowance = packageAllowanceDescription(item);
          return <div className="account-package" key={item.code}>
            <div className="account-package-info">
              <h3>{item.title}</h3>
              <strong>{money(item.price_rub)}</strong>
              <p>{allowance ?? "Состав пакета временно недоступен"}</p>
            </div>
            <Button className="account-package-action" variant="outline" size="lg" disabled={packagesDisabled}
              onClick={() => choose({ kind: "topup", code: item.code, title: item.title, price: item.price_rub, credit: item.credit_rub, allowance })}>Выбрать</Button>
          </div>;
        })}
      </div>
      {config.data && !config.data.enabled && <div className="account-notice pending">{config.data.reason ?? "Платёжный сервис недоступен"}</div>}
      <p className="account-hint account-packages-note">Разовое пополнение. Оплата на стороне ЮKassa.</p>
    </section>
    <AccountUsage />
  </div>;
}
