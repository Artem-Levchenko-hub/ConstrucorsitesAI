import type { BillingPlan } from "./api/account";

const plurals = new Intl.PluralRules("ru");
const forms = { edits: ["правка", "правки", "правок"], generations: ["генерация", "генерации", "генераций"] } as const;
export function allowanceCount(value: number, kind: keyof typeof forms) {
  const form = plurals.select(value);
  return `${value} ${forms[kind][form === "one" ? 0 : form === "few" ? 1 : 2]}`;
}
function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}
export function planAllowances(plan: BillingPlan) {
  const lifetime = plan.code === "free";
  return { lifetime,
    generations: count(plan.entitlements[lifetime ? "initial_generations_lifetime" : "initial_generations_monthly"]),
    edits: count(plan.entitlements[lifetime ? "edits_lifetime" : "edits_monthly"]),
  };
}
export function planAllowanceRows(plan: BillingPlan) {
  const { lifetime, generations, edits } = planAllowances(plan);
  const period = lifetime ? "за всё время" : "в месяц";
  return [
    ...(generations === undefined ? [] : [{ key: "generations", label: `Генераций ${period}`, value: generations }]),
    ...(edits === undefined ? [] : [{ key: "edits", label: `Правок ${period}`, value: edits }]),
    ...(generations === undefined && count(plan.entitlements.generation_messages_lifetime) !== undefined
      ? [{ key: "legacy-messages", label: "Сообщений для генерации за всё время", value: Number(plan.entitlements.generation_messages_lifetime) }] : []),
  ];
}
export function planAllowanceDescription(plan: BillingPlan) {
  const { lifetime, generations, edits } = planAllowances(plan);
  if (generations === undefined || edits === undefined) return undefined;
  return `${allowanceCount(generations, "generations")} и ${allowanceCount(edits, "edits")} ${lifetime ? "на аккаунт" : "в месяц"}`;
}
export function planPeriodDescription(plan: BillingPlan) {
  return plan.code === "free"
    ? "Бесплатные лимиты даются один раз на аккаунт и не обновляются каждый месяц."
    : "Месячные лимиты обновляются после каждой успешной ежемесячной оплаты тарифа.";
}
export function packageAllowanceDescription(item: { edit_units?: number; edits_only?: boolean }) {
  const units = count(item.edit_units);
  if (units === undefined) return undefined;
  return item.edits_only
    ? `${allowanceCount(units, "edits")} существующих приложений. Новые проекты не включены.`
    : `${allowanceCount(units, "edits")}. Первичная генерация расходует 5 правок. Пакет не добавляет мест для проектов.`;
}
