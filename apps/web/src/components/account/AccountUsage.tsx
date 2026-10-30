"use client";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { getBillingUsage, type BillingUsage, type EntitlementUsage } from "@/lib/api/account";
import { date, planDisplayName, QueryError } from "./account-presentation";
import "./account-usage.css";

const tabs = ["Сборки", "Списания", "Лимиты"] as const;
const periodLabel = (source: string) =>
  source === "subscription" ? "Оплаченный период" : source === "custom" ? "Выбранный период" : "Текущий месяц";
const percent = (used: number, limit: number) => limit > 0 ? Math.min(100, Math.max(0, used / limit * 100)) : 0;

function entitlementAllowance(item: EntitlementUsage): string {
  if (item.kind === "flag") return item.enabled === false ? "не входит в тариф" : "входит в тариф";
  return item.limit == null ? "Без ограничений" : String(item.limit);
}

function UsageMeter({ used, limit, label, valueText, exceeded = false }: {
  used: number; limit: number; label: string; valueText: string; exceeded?: boolean;
}) {
  // A zero or unlimited allowance cannot have a meaningful finite meter.
  if (limit <= 0) return null;
  return <div className={`usage-meter${exceeded ? " usage-meter-exceeded" : ""}`} role="meter"
    aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={Math.max(0, Math.min(used, limit))} aria-valuetext={valueText}>
    <span style={{ width: `${percent(used, limit)}%` }} />
  </div>;
}

function BuildsPanel({ data }: { data: BillingUsage }) {
  const { generations } = data;
  const outcomes = [
    { key: "completed", label: "Завершено", value: generations.completed },
    { key: "failed", label: "С ошибкой", value: generations.failed },
    { key: "active", label: "В работе", value: generations.active },
    { key: "cancelled", label: "Отменено", value: generations.cancelled },
  ];
  return <>
    <div className="usage-builds-hero">
      <div className="usage-hero">
        <p className="usage-eyebrow">Сборки приложений</p>
        <strong className="usage-hero-value">{generations.total}</strong>
        <p className="usage-caption">{generations.total === 0 ? "Пока нет сборок за период" : "за период"}</p>
      </div>
      <div className="usage-outcomes">
        <div className="usage-outcome-bar" aria-hidden="true">
          {outcomes.map(item => <span key={item.key} className={`usage-tone-${item.key}`} style={{ width: `${percent(item.value, generations.total)}%` }} />)}
        </div>
        <dl className="usage-outcome-legend">
          {outcomes.map(item => <div key={item.key}><dt><i className={`usage-tone-${item.key}`} aria-hidden="true" />{item.label}</dt><dd>{item.value}</dd></div>)}
        </dl>
      </div>
    </div>
    <div className="usage-footnotes">
      <div><span>Публикации</span><strong>{data.publications.total}</strong></div>
      <div><span>Опубликовано приложений</span><strong>{data.publications.projects}</strong></div>
    </div>
  </>;
}

function SpendingPanel({ data }: { data: BillingUsage }) {
  if (!data.credits) return <p className="usage-caption" role="status">Данные о списаниях временно недоступны</p>;
  return <div className="usage-credit-debits">
    <p className="usage-caption">Списано за период</p>
    <dl className="usage-debit-grid">
      <div><dt>Первичные генерации</dt><dd>{data.credits.initial_generations}</dd></div>
      <div><dt>Правки</dt><dd>{data.credits.edits}</dd></div>
    </dl>
    <p className="usage-caption usage-debit-note">Только подтверждённые списания</p>
  </div>;
}

function LimitsPanel({ data }: { data: BillingUsage }) {
  return <>
    <div className="usage-limits-heading">
      <p className="usage-eyebrow">Что включено в тариф</p><strong>{data.plan ? planDisplayName(data.plan) : "Ваш тариф"}</strong>
      <p className="usage-caption">Текущее использование не зависит от выбранного периода</p>
    </div>
    {data.entitlements.length === 0 ? <p className="usage-caption">Нет данных о лимитах</p> : <ul className="usage-limits">
      {data.entitlements.map(item => {
        const allowance = entitlementAllowance(item);
        return <li key={item.key} className={item.exceeded ? "usage-limit-exceeded" : undefined}>
          <p className="usage-limit-label">{item.label}</p>
          <dl className="usage-limit-values">
            <div><dt>Используется сейчас</dt><dd>{item.used}</dd></div>
            <div><dt>{item.kind === "flag" ? "Доступность" : "Лимит тарифа"}</dt><dd>{allowance}</dd></div>
          </dl>
          {item.kind === "limit" && item.limit != null && <UsageMeter used={item.used} limit={item.limit} label={item.label} valueText={`Используется сейчас: ${item.used}, лимит тарифа: ${allowance}${item.exceeded ? ", сверх тарифа" : ""}`} exceeded={item.exceeded} />}
          {item.exceeded && <em>сверх тарифа</em>}
        </li>;
      })}
    </ul>}
  </>;
}

/** Read-only period activity and current plan limits; tabs never request a charge. */
export function AccountUsage() {
  const usage = useQuery({ queryKey: ["billing-usage"], queryFn: () => getBillingUsage() });
  const [activeTab, setActiveTab] = useState(0);
  const id = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const data = usage.data;
  const showingLimits = activeTab === 2;
  const title = showingLimits ? "Лимиты тарифа" : "Расход за период";
  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
      : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    setActiveTab(next);
    tabRefs.current[next]?.focus();
  }
  return <section className="account-panel account-usage usage-dashboard" aria-label={title}>
    <div className="usage-heading"><h2>{title}</h2>
      {data && !showingLimits && <details className="usage-period"><summary>{periodLabel(data.period.source)}</summary><p><time dateTime={data.period.start}>{date(data.period.start)}</time><span> — </span><time dateTime={data.period.end}>{date(data.period.end)}</time>{data.plan && <span> · тариф {planDisplayName(data.plan)}</span>}</p></details>}
    </div>
    <QueryError error={usage.error} retry={() => void usage.refetch()} />
    {usage.isPending && <p className="usage-loading" role="status">Считаем расход…</p>}
    {data && <>
      <div className="usage-tabs" role="tablist" aria-label="Показатели аккаунта">
        {tabs.map((tab, index) => <button key={tab} type="button" role="tab" id={`${id}-tab-${index}`} aria-controls={`${id}-panel`}
          aria-selected={activeTab === index} tabIndex={activeTab === index ? 0 : -1} ref={element => { tabRefs.current[index] = element; }}
          onClick={() => setActiveTab(index)} onKeyDown={event => onTabKey(event, index)}>{tab}</button>)}
      </div>
      <div key={activeTab} className="usage-tab-panel" role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${activeTab}`} tabIndex={0}>
        {activeTab === 0 ? <BuildsPanel data={data} /> : activeTab === 1 ? <SpendingPanel data={data} /> : <LimitsPanel data={data} />}
      </div>
    </>}
  </section>;
}
