import { apiFetch } from "./client";
import { USE_MOCKS } from "./mocks";

/** New MAX projects require email proof and explicit current legal acceptances. */
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

export type MaxLegalAcceptance = {
  terms_accepted: boolean;
  privacy_accepted: boolean;
  personal_data_accepted: boolean;
  document_version: string;
};

export function acceptMaxDocuments(payload: MaxLegalAcceptance): Promise<{ accepted: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ accepted: true });
  return apiFetch<{ accepted: boolean }>("/api/max/account/acceptances", {
    method: "POST",
    json: payload,
  });
}

export function resendVerification(): Promise<{ accepted: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ accepted: true });
  return apiFetch<{ accepted: boolean }>("/api/auth/email/verify/request", {
    method: "POST",
  });
}

export function verifyEmailCode(code: string): Promise<{ verified: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ verified: /^\d{6}$/.test(code) });
  return apiFetch<{ verified: boolean }>("/api/auth/email/verify/code", {
    method: "POST",
    json: { code },
  });
}

export function verifyEmail(token: string): Promise<{ verified: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ verified: Boolean(token) });
  return apiFetch<{ verified: boolean }>("/api/auth/email/verify", {
    method: "POST",
    json: { token },
  });
}
