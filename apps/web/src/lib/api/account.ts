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
  package_code: string;
  amount_rub: string;
  credit_rub: string;
  status: string;
  confirmation_url: string | null;
  created_at: string;
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

export function exportAccount(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/api/account/export");
}

export function deleteAccount(): Promise<void> {
  return apiFetch<void>("/api/account", { method: "DELETE" });
}
