import type { CostBreakdown } from "@/lib/api/types";

const cost = (value: string | number) => `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 4 })} ₽`;
const callCount = (value: number) => {
  const ending = new Intl.PluralRules("ru-RU").select(value);
  return `${value} ${ending === "one" ? "вызов" : ending === "few" ? "вызова" : "вызовов"}`;
};

/** Source of the saved customer cost; never substitutes a provider expense. */
export function CostSource({ breakdown, calls, costRub, label }: {
  breakdown?: CostBreakdown | null;
  calls?: number;
  costRub: string | number;
  label: string;
}) {
  const buckets = breakdown && [breakdown.confirmed, breakdown.estimated, breakdown.unknown];
  const valid = buckets && buckets.every(bucket => bucket && Number.isSafeInteger(bucket.calls) && bucket.calls >= 0
    && typeof bucket.cost_rub === "string" && /^\d+(?:\.\d+)?$/.test(bucket.cost_rub) && Number.isFinite(Number(bucket.cost_rub)))
    && (calls === undefined || buckets.reduce((sum, bucket) => sum + bucket.calls, 0) === calls);
  const known = valid ? breakdown : null;
  const confirmed = known?.confirmed.calls ?? 0;
  const estimated = known?.estimated.calls ?? 0;
  const unknown = known ? known.unknown.calls : calls;
  const empty = known ? confirmed + estimated + (unknown ?? 0) === 0 : calls === 0 && Number(costRub) === 0;
  const mixed = known && [confirmed, estimated, unknown ?? 0].filter(count => count > 0).length > 1;
  const title = empty ? "Нет вызовов" : mixed ? "Смешанная стоимость" : confirmed > 0 ? "Подтверждено провайдером"
    : estimated > 0 ? "Оценочная стоимость" : "Источник стоимости неизвестен";
  return <span role="group" aria-label={`Источник стоимости: ${label}`} className="mt-1 block min-w-0 break-words text-[11px] font-normal leading-4 text-fg-tertiary">
    <span className="block">{title}</span>
    {mixed && confirmed > 0 && <span className="mt-1 block">Подтверждено провайдером: {callCount(confirmed)}, {cost(known!.confirmed.cost_rub)}</span>}
    {mixed && estimated > 0 && <span className="mt-1 block">Оценочная стоимость: {callCount(estimated)}, {cost(known!.estimated.cost_rub)}</span>}
    {!empty && (!known || (unknown ?? 0) > 0) && <span className="mt-1 block">История без подтверждённого источника: {unknown === undefined ? "число вызовов неизвестно" : callCount(unknown)}, {cost(known?.unknown.cost_rub ?? costRub)}</span>}
  </span>;
}
