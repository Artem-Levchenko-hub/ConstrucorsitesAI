"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowUpRight, ChevronDown, Receipt } from "lucide-react";
import { listPayments } from "@/lib/api/account";
import { date, money, paymentState, QueryError } from "./account-presentation";
import { paymentOutcome, paymentTitle } from "./payment-presentation";
import "./account-billing.css";

export function AccountTransactions() {
  const payments = useQuery({ queryKey: ["payments"], queryFn: listPayments });
  const [filter, setFilter] = useState("all");
  const rows = payments.data?.filter(p => filter === "all" || (filter === "pending"
    ? !paymentState(p.status).terminal
    : filter === "cancelled" ? ["cancelled", "canceled"].includes(p.status) : p.status === filter));
  return <section className="account-panel account-transactions" aria-label="История платежей">
    <div className="account-transactions-toolbar">
      <p className="account-transactions-scope">Последние 100 платежей</p>
      <label className="account-payment-filter"><span>Статус</span><span className="account-payment-select"><select aria-label="Статус платежей" value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="all">Все</option>
        <option value="succeeded">Успешные</option>
        <option value="pending">Незавершённые</option>
        <option value="cancelled">Отменённые</option>
        <option value="failed">Неуспешные</option>
        <option value="refunded">Возвраты</option>
      </select><ChevronDown size={16} aria-hidden="true" /></span>
      </label>
    </div>
    <QueryError error={payments.error} retry={() => void payments.refetch()} />{payments.isPending && <p role="status">Загружаем операции…</p>}
    {rows?.length === 0 && <div className="account-empty">
      <Receipt size={28} aria-hidden="true" />
      <h3>{payments.data?.length ? "Нет операций с этим статусом" : "Операций пока нет"}</h3>
      <p>{payments.data?.length ? "Выберите другой статус, чтобы увидеть остальные платежи." : "Здесь появятся покупки пакетов и тарифов."}</p>
      <Link href="/billing">К балансу →</Link>
    </div>}
    {Boolean(rows?.length) && <div className="account-receipts">
      <table role="table" aria-label="Платежи аккаунта">
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader" id="payment-date">Дата</th>
            <th scope="col" role="columnheader" id="payment-purpose">Операция</th>
            <th scope="col" role="columnheader" id="payment-amount">Сумма</th>
            <th scope="col" role="columnheader" id="payment-status">Статус</th>
          </tr>
        </thead>
        <tbody role="rowgroup">{rows?.map(p => {
          const state = paymentState(p.status);
          return <tr key={p.id} role="row">
          <td role="cell" headers="payment-date"><span className="account-operation-label" aria-hidden="true">Дата</span><time dateTime={p.created_at}>{date(p.created_at)}</time></td>
          <td role="cell" headers="payment-purpose"><span className="account-operation-label" aria-hidden="true">Операция</span><strong>{paymentTitle(p)}</strong>
            <small>{paymentOutcome(p)}</small>
          </td>
          <td role="cell" headers="payment-amount"><span className="account-operation-label" aria-hidden="true">Сумма</span><strong>{money(p.amount_rub)}</strong>
          </td>
          <td role="cell" headers="payment-status"><span className="account-operation-label" aria-hidden="true">Статус</span>
            <Link href={`/billing?payment=${encodeURIComponent(p.id)}`} className={`account-receipt-status ${state.tone}`}><span className="account-receipt-dot" aria-hidden="true" />{state.label}<ArrowUpRight size={14} aria-hidden="true" /></Link>
          </td>
        </tr>;
        })}</tbody>
      </table>
    </div>}
  </section>;
}
