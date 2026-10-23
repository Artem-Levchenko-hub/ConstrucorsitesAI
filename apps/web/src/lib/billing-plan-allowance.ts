import type { BillingPlan } from "./api/account";

export type GenerationAllowance = { creations: number; edits: number; period: "lifetime" | "paid_month" };

/** Explicit split entitlements identify new plans; historical credit plans stay separate. */
export function generationAllowance(plan?: Pick<BillingPlan, "entitlements"> | null): GenerationAllowance | null {
  const entitlements = plan?.entitlements;
  if (!entitlements) return null;
  const creations = entitlements.included_creations;
  const edits = entitlements.included_edits;
  const period = entitlements.generation_allowance_period;
  return typeof creations === "number" && Number.isInteger(creations) && creations >= 0
    && typeof edits === "number" && Number.isInteger(edits) && edits >= 0
    && (period === "lifetime" || period === "paid_month") ? { creations, edits, period } : null;
}

export const allowancePeriodLabel = (period: GenerationAllowance["period"]) => period === "lifetime" ? "За всё время" : "За оплаченный месяц";

export function generationAllowanceTerms(allowance: GenerationAllowance): string {
  return `Лимиты создания и правок учитываются отдельно на весь аккаунт. Техническая ошибка не расходует лимит. Запущенная генерация продолжится. ${allowance.period === "lifetime"
    ? "Лимиты даются один раз и не обновляются каждый месяц."
    : "После исчерпания соответствующего лимита новая операция станет доступна в следующем оплаченном периоде."}`;
}
