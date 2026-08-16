import { apiFetch } from "./client";
import { USE_MOCKS } from "./mocks";

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

export type BusinessReview = BusinessProfile & {
  owner_email: string;
};

let mockBusiness: BusinessProfile | null = null;

export function getMaxAccess(): Promise<MaxAccess> {
  if (USE_MOCKS) {
    return Promise.resolve({
      authenticated: true,
      email_verified: true,
      email_delivery_configured: true,
      business: mockBusiness,
      can_create_project: mockBusiness?.status === "verified",
      reason: mockBusiness ? null : "business_required",
      legal_document_version: "dev",
      payments_configured: true,
    });
  }
  return apiFetch<MaxAccess>("/api/max/account/access");
}

export function saveBusinessProfile(input: {
  kind: BusinessKind;
  inn: string;
  ogrn?: string;
  legal_name: string;
}): Promise<BusinessProfile> {
  if (USE_MOCKS) {
    mockBusiness = {
      id: "business-demo",
      kind: input.kind,
      inn: input.inn,
      ogrn: input.ogrn ?? null,
      legal_name: input.legal_name,
      status: "verified",
      verification_source: "mock",
      verification_note: null,
      verified_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
    };
    return Promise.resolve(mockBusiness);
  }
  return apiFetch<BusinessProfile>("/api/max/account/business", {
    method: "PUT",
    json: input,
  });
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

export function listBusinessReviews(): Promise<BusinessReview[]> {
  if (USE_MOCKS) {
    return Promise.resolve(
      mockBusiness
        ? [{ ...mockBusiness, owner_email: "demo@omnia.ai" }]
        : [],
    );
  }
  return apiFetch<BusinessReview[]>("/api/max/account/admin/businesses");
}

export function decideBusiness(
  inn: string,
  approved: boolean,
  note?: string,
): Promise<BusinessProfile> {
  if (USE_MOCKS) {
    if (!mockBusiness || mockBusiness.inn !== inn) {
      return Promise.reject(new Error("Заявка не найдена"));
    }
    mockBusiness = {
      ...mockBusiness,
      status: approved ? "verified" : "rejected",
      verification_source: "manual",
      verification_note: note ?? null,
      verified_at: approved ? new Date().toISOString() : null,
    };
    return Promise.resolve(mockBusiness);
  }
  return apiFetch<BusinessProfile>(
    `/api/max/account/business/${encodeURIComponent(inn)}/decision`,
    {
      method: "POST",
      json: { approved, note: note?.trim() || null },
    },
  );
}
