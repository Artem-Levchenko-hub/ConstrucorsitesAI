"use client";
import { Button } from "@/components/ui/button";
import { packageAllowanceDescription } from "@/lib/billing-allowances";
import { AccountBalance } from "./AccountBalance";
import { AccountUsage } from "./AccountUsage";
import type { PaymentJourney } from "./PaymentCheckout";
import { money, QueryError } from "./account-presentation";
export function AccountBilling({ journey: j }: { journey: PaymentJourney }) {
  return <div className="account-billing">
    <AccountBalance />
    <section className="account-panel" id="billing-packages" aria-label="Пакеты правок">
      <h2>Пополнить баланс</h2>
      <p className="account-hint">Выберите пакет, затем проверьте заказ перед оплатой.</p>
      <QueryError error={j.config.error} retry={() => void j.config.refetch()} />{j.config.isPending && <p role="status">Загружаем пакеты…</p>}
      {j.config.data?.packages.map(item => <div className="account-package" key={item.code}>
        <div>
          <h3>{item.title}</h3>
          <strong>{money(item.price_rub)}</strong>
          <p>{packageAllowanceDescription(item) ?? "Состав пакета временно недоступен"}</p>
        </div>
        <Button variant="outline" disabled={!j.config.data?.enabled || j.locked} onClick={() => j.choose({ kind: "topup", code: item.code, title: item.title, price: item.price_rub, credit: item.credit_rub, allowance: packageAllowanceDescription(item) })}>Выбрать</Button>
      </div>)}
      {j.config.data && !j.config.data.enabled && <div className="account-notice pending">{j.config.data.reason ?? "Платёжный сервис недоступен"}</div>}
      <p className="account-hint">Разовое пополнение. Оплата на стороне ЮKassa.</p>
    </section>
    <AccountUsage />
  </div>;
}
