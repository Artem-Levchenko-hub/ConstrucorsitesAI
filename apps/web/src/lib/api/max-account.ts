import { apiFetch } from "./client";
import { USE_MOCKS } from "./mocks";

/** An account is an email and a password; only the email needs confirming. */
export type MaxAccess = {
  authenticated: boolean;
  email_verified: boolean;
  email_delivery_configured: boolean;
  can_create_project: boolean;
  reason: string | null;
  legal_document_version: string;
  payments_configured: boolean;
};

export function getMaxAccess(): Promise<MaxAccess> {
  if (USE_MOCKS) {
    return Promise.resolve({
      authenticated: true,
      email_verified: true,
      email_delivery_configured: true,
      can_create_project: true,
      reason: null,
      legal_document_version: "dev",
      payments_configured: true,
    });
  }
  return apiFetch<MaxAccess>("/api/max/account/access");
}

export function resendVerification(email: string): Promise<{ accepted: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ accepted: Boolean(email) });
  return apiFetch<{ accepted: boolean }>("/api/auth/email/verify/request", {
    method: "POST",
    json: { email },
  });
}

export function verifyEmail(token: string): Promise<{ verified: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ verified: Boolean(token) });
  return apiFetch<{ verified: boolean }>("/api/auth/email/verify", {
    method: "POST",
    json: { token },
  });
}
