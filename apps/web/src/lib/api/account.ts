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
  is_lifetime: boolean;
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

export function createPayment(packageCode: string): Promise<Payment> {
  return apiFetch<Payment>("/api/payments", {
    method: "POST",
    json: {
      package_code: packageCode,
      idempotency_key: crypto.randomUUID(),
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
): Promise<Payment> {
  return apiFetch<Payment>("/api/payments/subscription", {
    method: "POST",
    json: {
      plan_code: planCode,
      idempotency_key: crypto.randomUUID(),
      auto_renew: autoRenew,
      consent_version: autoRenew
        ? process.env.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION ?? "2026-07-30"
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
          ? process.env.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION ?? "2026-07-30"
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
