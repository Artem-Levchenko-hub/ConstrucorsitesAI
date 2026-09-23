"use client";
import { useQuery } from "@tanstack/react-query";
import { getBillingUsage, type EntitlementUsage } from "@/lib/api/account";
import { date, money, QueryError } from "./account-presentation";

/**
 * "Расход за период": what the account spent and how much of the plan it uses,
 * straight from GET /api/billing/usage. The period is the paid month or, on
 * Free, the current calendar month.
 */
const periodLabel = (source: string) =>
  source === "subscription" ? "текущий оплаченный период" : source === "custom" ? "выбранный период" : "текущий месяц";

function entitlementValue(item: EntitlementUsage): string {
  if (item.kind === "flag") return item.enabled === false ? "не входит в тариф" : `подключено: ${item.used}`;
  if (item.limit === null || item.limit === undefined) return `${item.used} · без ограничений`;
  return `${item.used} из ${item.limit}`;
}

export function AccountUsage() {
  const usage = useQuery({ queryKey: ["billing-usage"], queryFn: () => getBillingUsage() });
  const data = usage.data;
  return <section className="account-panel account-usage" aria-label="Расход за период">
    <h2>Расход за период</h2>
    <QueryError error={usage.error} retry={() => void usage.refetch()} />
    {usage.isPending && <p role="status">Считаем расход…</p>}
    {data && <>
      <p className="account-hint">
        {periodLabel(data.period.source)}: {date(data.period.start)} — {date(data.period.end)}
        {data.plan ? ` · тариф ${data.plan.name}` : ""}
      </p>
      <dl className="account-usage-grid">
        <div>
          <dt>Сборки приложений</dt>
          <dd>{data.generations.total}<small>{money(data.generations.cost_rub)} за модели · завершено {data.generations.completed}, с ошибкой {data.generations.failed}</small></dd>
        </div>
        <div>
          <dt>Ответы ИИ посетителям приложений</dt>
          <dd>{data.app_ai_answers.calls}<small>{money(data.app_ai_answers.cost_rub)}</small></dd>
        </div>
        <div>
          <dt>Публикации</dt>
          <dd>{data.publications.total}<small>приложений опубликовано: {data.publications.projects}</small></dd>
        </div>
        <div>
          <dt>Списано с баланса</dt>
          <dd>{money(data.wallet.debited_rub)}<small>зачислено {money(data.wallet.credited_rub)} · операций {data.wallet.charges}</small></dd>
        </div>
        <div>
          <dt>Бесплатные сборки</dt>
          <dd>{data.free_generations.unlimited ? "без ограничений" : `осталось ${data.free_generations.left} из ${data.free_generations.limit}`}</dd>
        </div>
      </dl>
      <h3>Что включено в тариф и сколько уже используется</h3>
      <ul className="account-usage-entitlements">
        {data.entitlements.map(item => <li key={item.key} className={item.exceeded ? "exceeded" : undefined}>
          <span>{item.label}</span>
          <strong>{entitlementValue(item)}</strong>
          {item.exceeded && <em>сверх тарифа</em>}
        </li>)}
      </ul>
    </>}
  </section>;
}
