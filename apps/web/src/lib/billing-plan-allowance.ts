import type { BillingPlan } from "./api/account";

/** The entitlement distinguishes included-generation plans from legacy credit plans. */
export function includedGenerationLimit(plan: Pick<BillingPlan, "entitlements">): number | null {
  const count = plan.entitlements.included_generations;
  return typeof count === "number" && Number.isInteger(count) && count >= 0 ? count : null;
}

export const INCLUDED_GENERATION_TERMS = "В общий лимит на весь аккаунт входят создание и правки — без списания с баланса. Техническая ошибка не расходует генерацию. После исчерпания лимита новая генерация доступна в следующем оплаченном периоде; запущенная продолжится.";
