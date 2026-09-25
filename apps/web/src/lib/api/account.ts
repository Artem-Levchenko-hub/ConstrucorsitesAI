import { apiFetch } from "./client";

export type AuthSession = {
  id: string;
  current: boolean;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_seen_at: string;
};

export type PaymentConfig = {
  enabled: boolean;
  reason: string | null;
  packages: Array<{
    code: "start" | "business" | "pro";
    price_rub: string;
    credit_rub: string;
    title: string;
  }>;
};

export type Payment = {
  id: string;
  purpose: "wallet_topup" | "subscription_initial" | "subscription_renewal";
  subscription_id: string | null;
  package_code: string;
  amount_rub: string;
  credit_rub: string;
  status: string;
  confirmation_url: string | null;
  created_at: string;
};

export type BillingPlan = {
  id: string;
  code: "free" | "pro" | "business";
  version: number;
  name: string;
  price_rub: string;
  billing_interval: "month";
  included_credit_rub: string;
  entitlements: Record<string, unknown>;
};

export type Subscription = {
  id: string;
  status: "trialing" | "active" | "past_due" | "paused";
  auto_renew: boolean;
  cancel_at_period_end: boolean;
  current_period_start: string | null;
  current_period_end: string | null;
  next_charge_at: string | null;
  grace_period_ends_at: string | null;
  renewal_consent_version: string | null;
  can_restore: boolean;
  canceled_at: string | null;
  ended_at: string | null;
  created_at: string;
  plan: BillingPlan;
};

/** One kind of AI spend in the period (gateway ledger rows). */
export type UsageAIBucket = {
  calls: number;
  cost_rub: string;
  tokens_in: number;
  tokens_out: number;
};

export type EntitlementUsage = {
  key: string;
  label: string;
  kind: "limit" | "flag";
  // null = no limit on this plan (kind "limit" only).
  limit?: number | null;
  // kind "flag" only: whether the plan includes the feature.
  enabled?: boolean | null;
  used: number;
  exceeded: boolean;
};

/** GET /api/billing/usage — the account's spend journal for a period. */
export type BillingUsage = {
  period: { start: string; end: string; source: "subscription" | "calendar_month" | "custom" };
  plan: BillingPlan | null;
  subscription_status: string | null;
  generations: UsageAIBucket & {
    total: number;
    completed: number;
    failed: number;
    cancelled: number;
    active: number;
  };
  app_ai_answers: UsageAIBucket;
  other_ai: UsageAIBucket;
  publications: { total: number; projects: number };
  free_generations: { limit: number; used: number; left: number; unlimited: boolean };
  wallet: { balance_rub: string; debited_rub: string; credited_rub: string; charges: number };
  entitlements: EntitlementUsage[];
  total_ai_cost_rub: string;
};

export function getBillingUsage(range?: { from?: string; to?: string }): Promise<BillingUsage> {
  const params = new URLSearchParams();
  if (range?.from) params.set("from", range.from);
  if (range?.to) params.set("to", range.to);
  const query = params.toString();
  return apiFetch<BillingUsage>(`/api/billing/usage${query ? `?${query}` : ""}`);
}

export function listSessions(): Promise<AuthSession[]> {
  return apiFetch<AuthSession[]>("/api/auth/sessions");
}

export function revokeSession(id: string): Promise<void> {
  return apiFetch<void>(`/api/auth/sessions/${id}`, { method: "DELETE" });
}

export function getPaymentConfig(): Promise<PaymentConfig> {
  return apiFetch<PaymentConfig>("/api/payments/config");
}

export function listPayments(): Promise<Payment[]> {
  return apiFetch<Payment[]>("/api/payments");
}

export function createPayment(packageCode: string, idempotencyKey = crypto.randomUUID()): Promise<Payment> {
  return apiFetch<Payment>("/api/payments", {
    method: "POST",
    json: {
      package_code: packageCode,
      idempotency_key: idempotencyKey,
    },
  });
}

export function listBillingPlans(): Promise<BillingPlan[]> {
  return apiFetch<BillingPlan[]>("/api/billing/plans");
}

export function getSubscription(): Promise<Subscription> {
  return apiFetch<Subscription>("/api/billing/subscription");
}

export function createSubscriptionCheckout(
  planCode: "pro" | "business",
  autoRenew: boolean,
  idempotencyKey = crypto.randomUUID(),
): Promise<Payment> {
  return apiFetch<Payment>("/api/payments/subscription", {
    method: "POST",
    json: {
      plan_code: planCode,
      idempotency_key: idempotencyKey,
      auto_renew: autoRenew,
      consent_version: autoRenew
        ? process.env.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION ?? "2026-09-25"
        : null,
    },
  });
}

export function manageSubscription(
  action: "cancel" | "restore",
): Promise<Subscription> {
  return apiFetch<Subscription>("/api/billing/subscription", {
    method: "PATCH",
    json: {
      action,
      consent_version:
        action === "restore"
          ? process.env.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION ?? "2026-09-25"
          : null,
    },
  });
}

export function exportAccount(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/api/account/export");
}

export function deleteAccount(): Promise<void> {
  return apiFetch<void>("/api/account", { method: "DELETE" });
}
