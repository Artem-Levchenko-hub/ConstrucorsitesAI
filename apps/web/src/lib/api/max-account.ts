import { apiFetch } from "./client";

export type BusinessKind =
  | "legal_entity"
  | "sole_proprietor"
  | "self_employed";

export type BusinessProfile = {
  id: string;
  kind: BusinessKind;
  inn: string;
  ogrn: string | null;
  legal_name: string;
  status: "pending" | "verified" | "rejected" | "suspended";
  verification_source: string | null;
  verification_note: string | null;
  verified_at: string | null;
  created_at: string;
};

export type MaxAccess = {
  authenticated: boolean;
  email_verified: boolean;
  email_delivery_configured: boolean;
  business: BusinessProfile | null;
  can_create_project: boolean;
  reason: string | null;
  legal_document_version: string;
  payments_configured: boolean;
};

export function getMaxAccess(): Promise<MaxAccess> {
  return apiFetch<MaxAccess>("/api/max/account/access");
}

export function saveBusinessProfile(input: {
  kind: BusinessKind;
  inn: string;
  ogrn?: string;
  legal_name: string;
}): Promise<BusinessProfile> {
  return apiFetch<BusinessProfile>("/api/max/account/business", {
    method: "PUT",
    json: input,
  });
}

export function resendVerification(email: string): Promise<{ accepted: boolean }> {
  return apiFetch<{ accepted: boolean }>("/api/auth/email/verify/request", {
    method: "POST",
    json: { email },
  });
}

export function verifyEmail(token: string): Promise<{ verified: boolean }> {
  return apiFetch<{ verified: boolean }>("/api/auth/email/verify", {
    method: "POST",
    json: { token },
  });
}
