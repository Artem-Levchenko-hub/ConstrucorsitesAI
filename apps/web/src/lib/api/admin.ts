import type { BusinessProfile } from "./max-account";
import { apiFetch } from "./client";

export type AdminUser = {
  id: string;
  email: string;
  role: "user" | "admin";
  is_admin: boolean;
  status: string;
  email_verified_at: string | null;
  created_at: string;
  last_login_at: string | null;
  wallet_balance_rub: string;
  business: BusinessProfile | null;
};

export type AdminUserUpdate = {
  role?: "user" | "admin";
  email_verified?: boolean;
  status?: "active" | "suspended";
  business_verified?: boolean;
  note?: string;
};

export type AdminAuditEvent = {
  id: string;
  actor_email: string;
  target_email: string;
  action: string;
  details: {
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
    note?: string | null;
  };
  created_at: string;
};

export function listAdminUsers(query = ""): Promise<AdminUser[]> {
  const params = query.trim()
    ? `?query=${encodeURIComponent(query.trim())}`
    : "";
  return apiFetch<AdminUser[]>(`/api/admin/users${params}`);
}

export function updateAdminUser(
  userId: string,
  update: AdminUserUpdate,
): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/api/admin/users/${userId}`, {
    method: "PATCH",
    json: update,
  });
}

export function listAdminAudit(): Promise<AdminAuditEvent[]> {
  return apiFetch<AdminAuditEvent[]>("/api/admin/audit");
}
