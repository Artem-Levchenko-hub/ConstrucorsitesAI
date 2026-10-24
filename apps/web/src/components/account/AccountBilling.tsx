"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { getWallet } from "@/lib/api/wallet";
import { getGenerationCredits } from "@/lib/api/account";
import { allowanceCount, packageAllowanceDescription } from "@/lib/billing-allowances";
import { AccountUsage } from "./AccountUsage";
import type { PaymentJourney } from "./PaymentCheckout";
import { money, QueryError } from "./account-presentation";
export function AccountBilling({ journey: j }: { journey: PaymentJourney }) {
  const wallet = useQuery({ queryKey: ["wallet"], queryFn: getWallet });
  const credits = useQuery({ queryKey: ["billing-credits"], queryFn: getGenerationCredits });
  return <div className="account-billing">
    <section className="account-wallet">
      <p>Доступно для работы</p>
      <h2>{credits.data ? allowanceCount(credits.data.available_units + credits.data.included_edits_remaining, "edits") : credits.isError ? "Недоступно" : "Загрузка…"}</h2>
      {credits.data && <>
        <p>По тарифу: {allowanceCount(credits.data.included_generations_remaining, "generations")} и {allowanceCount(credits.data.included_edits_remaining, "edits")}.</p>
        <p>В пакетах: {allowanceCount(credits.data.available_units, "edits")}, из них только для правок — {credits.data.edit_only_units}. Первичная генерация расходует 5 правок Бизнес-пакета или Профессионального пакета.</p>
        {credits.data.reserved_units > 0 && <p>Зарезервировано выполняющимися запросами: {credits.data.reserved_units}. Эти единицы уже исключены из доступного остатка.</p>}
      </>}
      {wallet.data && Number(wallet.data.balance_rub) > 0 && <p>Денежный остаток по прежним условиям: {money(wallet.data.balance_rub)}</p>}
      <QueryError error={credits.error} retry={() => void credits.refetch()} />
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
