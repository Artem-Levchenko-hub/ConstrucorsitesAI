"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Boxes, ChevronDown, PencilLine, WalletCards } from "lucide-react";
import Link from "next/link";
import { getGenerationCredits } from "@/lib/api/account";
import { allowanceCount } from "@/lib/billing-allowances";
import { date, QueryError } from "./account-presentation";
import "./account-balance.css";

function PlanRemainder({ remaining, limit, label }: { remaining: number; limit: number; label: string }) {
  const value = Math.min(Math.max(remaining, 0), limit);
  return <div className="balance-plan">
    <span>По тарифу <strong>{remaining}<span> / {limit}</span></strong></span>
    <span className="balance-track" role={limit > 0 ? "meter" : undefined} aria-label={limit > 0 ? label : undefined}
      aria-valuemin={limit > 0 ? 0 : undefined} aria-valuemax={limit > 0 ? limit : undefined}
      aria-valuenow={limit > 0 ? value : undefined}>
      <span style={{ width: `${limit > 0 ? value / limit * 100 : 0}%` }} />
    </span>
  </div>;
}

/** Availability in the product's two units: initial generations and edits. */
export function AccountBalance() {
  const credits = useQuery({ queryKey: ["billing-credits"], queryFn: getGenerationCredits });
  const data = credits.data;
  // Inventory has already excluded held units. Flexible units are shared between
  // new builds and edits; an edits-only package must never increase new builds.
  const packageBuilds = data && data.initial_build_units > 0 ? Math.floor(data.flexible_units / data.initial_build_units) : 0;
  const packageEdits = data && data.edit_units > 0 ? Math.floor(data.available_units / data.edit_units) : 0;
  const builds = data ? data.included_generations_remaining + packageBuilds : undefined;
  const edits = data ? data.included_edits_remaining + packageEdits : undefined;
  const exhausted = builds === 0 && edits === 0;

  return <section className="account-wallet balance-card" aria-label="Доступно для работы" aria-busy={credits.isPending}>
    <header className="balance-heading">
      <span className="balance-icon"><WalletCards size={19} aria-hidden="true" /></span>
      <h2>Доступно для работы</h2>
      {data && <span className={`balance-state${exhausted ? " is-empty" : ""}`}><span />{exhausted ? "Лимиты исчерпаны" : builds === 0 ? "Доступны правки" : "Можно создавать"}</span>}
    </header>
    <div className="balance-metrics" role="group" aria-label="Остатки генераций и правок">
      <div className="balance-metric">
        <span className="balance-label"><Boxes size={16} aria-hidden="true" />Генерации</span>
        <strong className="balance-number" aria-label="Доступно генераций">{builds?.toLocaleString("ru-RU") ?? "—"}</strong>
        {data && <PlanRemainder remaining={data.included_generations_remaining} limit={data.included_generations_limit} label="Осталось генераций по тарифу" />}
      </div>
      <div className="balance-metric">
        <span className="balance-label"><PencilLine size={16} aria-hidden="true" />Правки</span>
        <strong className="balance-number" aria-label="Доступно правок">{edits?.toLocaleString("ru-RU") ?? "—"}</strong>
        {data && <PlanRemainder remaining={data.included_edits_remaining} limit={data.included_edits_limit} label="Осталось правок по тарифу" />}
      </div>
    </div>
    {credits.isPending && <p className="balance-loading" role="status">Загружаем остатки…</p>}
    <QueryError error={credits.error} retry={() => void credits.refetch()} />
    {data && <details className="balance-details">
      <summary><span>Подробности{data.available_units > 0 && <small>В пакетах: {allowanceCount(packageEdits, "edits")}</small>}</span><ChevronDown size={16} aria-hidden="true" /></summary>
      <dl>
        <div><dt>В пакетах</dt><dd>{allowanceCount(packageEdits, "edits")}</dd></div>
        <div><dt>Только для правок</dt><dd>{data.edit_only_units}</dd></div>
        {data.reserved_units > 0 && <div><dt>В работе · уже учтено</dt><dd>{data.reserved_units}</dd></div>}
      </dl>
      <p>Пакет общий для генераций и правок. Новая генерация — {data.initial_build_units} единиц, правка — {data.edit_units}.</p>
      {data.period_end && <p>Период тарифа до <time dateTime={data.period_end}>{date(data.period_end)}</time>.</p>}
      <p>Начисления — после подтверждения оплаты.</p>
    </details>}
    <footer className="balance-actions">
      <a href="#billing-packages" className="balance-topup">Выбрать пакет<ArrowUpRight size={17} aria-hidden="true" /></a>
      <Link href="/billing/transactions">Операции<ArrowUpRight size={16} aria-hidden="true" /></Link>
    </footer>
  </section>;
}
