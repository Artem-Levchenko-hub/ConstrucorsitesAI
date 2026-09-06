"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { getWallet } from "@/lib/api/wallet";
import type { PaymentJourney } from "./PaymentCheckout";
import { money, QueryError } from "./account-presentation";
export function AccountBilling({ journey: j }: { journey: PaymentJourney }) {
  const wallet = useQuery({ queryKey: ["wallet"], queryFn: getWallet });
  return <div className="account-billing">
    <section className="account-wallet">
      <p>Доступно на балансе</p>
      <h2>{wallet.data ? money(wallet.data.balance_rub) : wallet.isError ? "Недоступно" : "Загрузка…"}</h2>
      <p>Средства на использование MAX Studio</p>
      <QueryError error={wallet.error} retry={() => void wallet.refetch()} />
      <div className="account-wallet-footer">
        <p>Начисления появятся после подтверждения оплаты.</p>
        <Link href="/billing/transactions">История операций →</Link>
      </div>
    </section>
    <section className="account-panel">
      <h2>Пополнить баланс</h2>
      <p className="account-hint">Выберите пакет, затем проверьте заказ перед оплатой.</p>
      <QueryError error={j.config.error} retry={() => void j.config.refetch()} />{j.config.isPending && <p role="status">Загружаем пакеты…</p>}
      {j.config.data?.packages.map(item => <div className="account-package" key={item.code}>
        <div>
          <h3>{item.title}</h3>
          <strong>{money(item.price_rub)}</strong>
          <p>Зачислим {money(item.credit_rub)}</p>
        </div>
        <Button variant="outline" disabled={!j.config.data?.enabled || j.locked} onClick={() => j.choose({ kind: "topup", code: item.code, title: item.title, price: item.price_rub, credit: item.credit_rub })}>Выбрать</Button>
      </div>)}
      {j.config.data && !j.config.data.enabled && <div className="account-notice pending">{j.config.data.reason ?? "Платёжный сервис недоступен"}</div>}
      <p className="account-hint">Разовое пополнение. Оплата на стороне ЮKassa.</p>
    </section>
  </div>;
}
