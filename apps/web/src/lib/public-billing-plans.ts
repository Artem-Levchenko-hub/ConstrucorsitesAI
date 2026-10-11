import "server-only";

import type { BillingPlan } from "@/lib/api/account";

function isPlan(value: unknown): value is BillingPlan {
  if (!value || typeof value !== "object") return false;
  const plan = value as Partial<BillingPlan>;
  const amount = (value: unknown) => typeof value === "string" && /^\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value));
  return typeof plan.id === "string" && typeof plan.name === "string"
    && ["free", "pro", "business"].includes(String(plan.code))
    && Number.isInteger(plan.version) && Number(plan.version) > 0
    && plan.billing_interval === "month"
    && amount(plan.price_rub) && amount(plan.included_credit_rub)
    && !!plan.entitlements && typeof plan.entitlements === "object"
    && !Array.isArray(plan.entitlements)
    && ["max_projects", "static_publish_slots"].every(key => plan.entitlements?.[key] === null || (typeof plan.entitlements?.[key] === "number" && Number.isInteger(plan.entitlements[key]) && Number(plan.entitlements[key]) >= 0));
}

/** Public catalog: no cookies, no stale prices, no invented fallback on failure. */
export async function getPublicBillingPlans(): Promise<BillingPlan[] | null> {
  const base = (process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}/api/billing/plans`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return null;
    const plans: unknown = await response.json();
    if (!Array.isArray(plans) || plans.length === 0 || !plans.every(isPlan)) return null;
    return plans;
  } catch {
    return null;
  }
}
