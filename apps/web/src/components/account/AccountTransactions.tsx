"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { listPayments } from "@/lib/api/account";
import { date, money, paymentState, QueryError } from "./account-presentation";
export function AccountTransactions() {
  const payments = useQuery({ queryKey: ["payments"], queryFn: listPayments });
  const [filter, setFilter] = useState("all");
  const rows = payments.data?.filter(p => filter === "all" || (filter === "pending" ? !paymentState(p.status).terminal : p.status === filter));
  return <section className="account-panel">
    <div className="account-section-heading">
      <h2>История операций</h2>
      <label>Статус <select value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="all">Все</option>
        <option value="succeeded">Успешные</option>
        <option value="pending">Незавершённые</option>
        <option value="cancelled">Отменённые</option>
        <option value="failed">Неуспешные</option>
        <option value="refunded">Возвраты</option>
      </select>
      </label>
    </div>
    <p className="account-hint">Последние 100 платежей аккаунта. Статусы и суммы получены с сервера.</p>
    <QueryError error={payments.error} retry={() => void payments.refetch()} />{payments.isPending && <p role="status">Загружаем операции…</p>}
    {rows?.length === 0 && <div className="account-empty">
      <h3>{payments.data?.length ? "Нет операций с этим статусом" : "Операций пока нет"}</h3>
      <p>Здесь появятся платежи за пополнения и тарифы.</p>
      <Link href="/billing">К балансу →</Link>
    </div>}
    {Boolean(rows?.length) && <div className="account-table-scroll">
      <table>
        <thead>
          <tr>
            <th>Дата</th>
            <th>Операция</th>
            <th>Сумма</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>{rows?.map(p => <tr key={p.id}>
          <td>{date(p.created_at)}</td>
          <td>{p.purpose === "wallet_topup" ? "Пополнение баланса" : p.purpose === "subscription_renewal" ? "Продление тарифа" : "Покупка тарифа"}
            <small>{p.package_code}</small>
          </td>
          <td>{money(p.amount_rub)}
            <small>{p.status === "succeeded" ? `Кредит: ${money(p.credit_rub)}` : "Не зачислено"}</small>
          </td>
          <td>
            <Link href={`/billing?payment=${encodeURIComponent(p.id)}`} className={`account-status ${paymentState(p.status).tone}`}>{paymentState(p.status).label}</Link>
          </td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
