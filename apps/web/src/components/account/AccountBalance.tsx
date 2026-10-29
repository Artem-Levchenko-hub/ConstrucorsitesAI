"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, BadgeCheck, Boxes, CalendarDays, ChevronDown, Clock3, Equal, PencilLine, WalletCards } from "lucide-react";
import Link from "next/link";
import { getBillingUsage, getGenerationCredits, type GenerationCredits } from "@/lib/api/account";
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

function CreditDetails({ data, generations, edits }: { data: GenerationCredits; generations: number; edits: number }) {
  const editOnly = data.edit_units > 0 ? Math.floor(data.edit_only_units / data.edit_units) : 0;
  const exchange = data.edit_units > 0 ? data.initial_build_units / data.edit_units : 0;
  const hasExchange = exchange > 0 && Number.isInteger(exchange);

  return <details className="balance-details">
    <summary><span>Подробности</span><ChevronDown size={16} aria-hidden="true" /></summary>
    <div className="balance-details-body">
      {data.available_units > 0 ? <div className="balance-package-breakdown">
        <p className="balance-caption">Из пакетов</p>
        <dl className="balance-package-grid" aria-label="Лимиты из пакетов">
          <div><dt>Генерации</dt><dd>{generations.toLocaleString("ru-RU")}</dd></div>
          <div><dt>Правки</dt><dd>{edits.toLocaleString("ru-RU")}</dd></div>
        </dl>
        <p className="balance-package-choice">Общий остаток: генерации или правки</p>
        {editOnly > 0 && <p className="balance-edit-only"><PencilLine size={16} aria-hidden="true" />Из них {editOnly} — только для правок</p>}
      </div> : <div className="balance-package-empty"><Boxes size={20} aria-hidden="true" /><p>В пакетах пока нет правок</p></div>}
      {hasExchange && <div className="balance-conversion" aria-label="Расход общего пакета">
        <span><Boxes size={16} aria-hidden="true" />1 генерация</span>
        <Equal size={16} aria-label="равно" />
        <span><PencilLine size={16} aria-hidden="true" />{allowanceCount(exchange, "edits")}</span>
      </div>}
      <div className="balance-details-meta">
        {data.reserved_units > 0 && <div className="balance-reserve"><Clock3 size={16} aria-hidden="true" /><span>Запросы в работе<small>Учтено в остатке</small></span></div>}
        {data.period_end && <div className="balance-period"><CalendarDays size={16} aria-hidden="true" /><span>Период тарифа до<time dateTime={data.period_end}>{date(data.period_end)}</time></span></div>}
      </div>
      <p className="balance-payment-note"><BadgeCheck size={16} aria-hidden="true" /><span>Пакет начислим после подтверждения оплаты.</span></p>
    </div>
  </details>;
}

/** Availability in the product's two units: initial generations and edits. */
export function AccountBalance() {
  const credits = useQuery({ queryKey: ["billing-credits"], queryFn: getGenerationCredits });
  const usage = useQuery({ queryKey: ["billing-usage"], queryFn: () => getBillingUsage() });
  const unlimited = usage.data?.free_generations?.unlimited === true;
  const data = credits.data;
  // Inventory has already excluded held units. Flexible units are shared between
  // new builds and edits; an edits-only package must never increase new builds.
  const packageBuilds = data && data.initial_build_units > 0 ? Math.floor(data.flexible_units / data.initial_build_units) : 0;
  const packageEdits = data && data.edit_units > 0 ? Math.floor(data.available_units / data.edit_units) : 0;
  const builds = data ? data.included_generations_remaining + packageBuilds : undefined;
  const edits = data ? data.included_edits_remaining + packageEdits : undefined;
  const exhausted = !unlimited && builds === 0 && edits === 0;
  const displayCount = (remaining: number | undefined) => unlimited ? "∞" : remaining?.toLocaleString("ru-RU") ?? "—";

  return <section className="account-wallet balance-card" aria-label="Доступно для работы" aria-busy={credits.isPending}>
    <header className="balance-heading">
      <span className="balance-icon"><WalletCards size={19} aria-hidden="true" /></span>
      <h2>Доступно для работы</h2>
      {data && <span className={`balance-state${exhausted ? " is-empty" : ""}`}><span />{unlimited ? "Без ограничений" : exhausted ? "Лимиты исчерпаны" : builds === 0 ? "Доступны правки" : "Можно создавать"}</span>}
    </header>
    <div className="balance-metrics" role="group" aria-label="Остатки генераций и правок">
      <div className="balance-metric">
        <span className="balance-label"><Boxes size={16} aria-hidden="true" />Генерации</span>
        <strong className="balance-number" aria-label="Доступно генераций">{displayCount(builds)}</strong>
        {data && !unlimited && <PlanRemainder remaining={data.included_generations_remaining} limit={data.included_generations_limit} label="Осталось генераций по тарифу" />}
      </div>
      <div className="balance-metric">
        <span className="balance-label"><PencilLine size={16} aria-hidden="true" />Правки</span>
        <strong className="balance-number" aria-label="Доступно правок">{displayCount(edits)}</strong>
        {data && !unlimited && <PlanRemainder remaining={data.included_edits_remaining} limit={data.included_edits_limit} label="Осталось правок по тарифу" />}
      </div>
    </div>
    {credits.isPending && <p className="balance-loading" role="status">Загружаем остатки…</p>}
    <QueryError error={credits.error} retry={() => void credits.refetch()} />
    {data && <CreditDetails data={data} generations={packageBuilds} edits={packageEdits} />}
    <footer className="balance-actions">
      <a href="#billing-packages" className="balance-topup">Выбрать пакет<ArrowUpRight size={17} aria-hidden="true" /></a>
      <Link href="/billing/transactions">Операции<ArrowUpRight size={16} aria-hidden="true" /></Link>
    </footer>
  </section>;
}
