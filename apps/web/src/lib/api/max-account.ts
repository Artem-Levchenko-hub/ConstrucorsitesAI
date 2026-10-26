import { apiFetch } from "./client";
import { USE_MOCKS } from "./mocks";

/** Access follows the server's one-time phone policy and current legal acceptances. */
export type RegistrationVerificationReason = "phone_verification_required" | "email_verification_required" | null;

export type MaxAccess = {
  authenticated: boolean;
  email_verified: boolean;
  email_delivery_configured: boolean;
  phone_verified: boolean;
  phone_verification_required: boolean;
  phone_verification_available: boolean;
  registration_verification_reason?: RegistrationVerificationReason;
  can_create_project: boolean;
  reason: string | null;
  legal_document_version: string;
  payments_configured: boolean;
};

/** Older API revisions have no canonical reason; retain their email proof policy. */
export function registrationVerificationReason(access: MaxAccess): RegistrationVerificationReason {
  if (access.registration_verification_reason !== undefined) return access.registration_verification_reason;
  if (access.phone_verification_required) return "phone_verification_required";
  return access.phone_verified || access.email_verified ? null : "email_verification_required";
}

export function getMaxAccess(): Promise<MaxAccess> {
  if (USE_MOCKS) {
    return Promise.resolve({
      authenticated: true,
      email_verified: true,
      email_delivery_configured: true,
      phone_verified: true,
      phone_verification_required: false,
      phone_verification_available: true,
      registration_verification_reason: null,
      can_create_project: true,
      reason: null,
      legal_document_version: "dev",
      payments_configured: true,
    });
  }
  return apiFetch<MaxAccess>("/api/max/account/access");
}

export function requestPhoneVerification(phone: string): Promise<{ accepted: boolean; expires_in: number; resend_after: number }> {
  if (USE_MOCKS) return Promise.resolve({ accepted: true, expires_in: 600, resend_after: 60 });
  return apiFetch("/api/auth/phone/request", { method: "POST", json: { phone } });
}

export function verifyPhoneCode(code: string): Promise<{ verified: boolean }> {
  if (USE_MOCKS) return Promise.resolve({ verified: /^\d{6}$/.test(code) });
  return apiFetch("/api/auth/phone/verify", { method: "POST", json: { code } });
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
