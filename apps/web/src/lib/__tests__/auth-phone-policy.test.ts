import { afterEach, expect, it, vi } from "vitest";
import { getSession } from "@/lib/auth-mock";
import { registrationVerificationReason, type MaxAccess } from "@/lib/api/max-account";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => ({ value: "session-under-test" }) }) }));
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each([
  [{ registration_verification_reason: "phone_verification_required", phone_verification_required: true, email_verified_at: "2026-10-01" }, "phone_verification_required"],
  [{ registration_verification_reason: "email_verification_required", phone_verification_required: false }, "email_verification_required"],
  [{ registration_verification_reason: null, phone_e164: "+79991234567", phone_verified_at: "2026-10-01" }, null],
  [{ phone_verification_required: false }, "email_verification_required"],
  [{ phone_e164: "+79991234567", phone_verified_at: "2026-10-01" }, null],
  [{ phone_verified_at: "2026-10-01" }, "email_verification_required"],
])("maps server identity proof policy into the session (%j)", async (proof, expected) => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCKS", "false");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ id: "owner", email: "owner@example.com", is_anon: false, email_verified_at: null, ...proof })));
  expect((await getSession())?.registrationVerificationReason).toBe(expected);
});

it.each([
  [{ phone_verified: true }, null],
  [{ phone_verification_required: true, email_verified: true }, "phone_verification_required"],
  [{ phone_verified: false, email_verified: false }, "email_verification_required"],
  [{ email_verified: true }, null],
  [{ registration_verification_reason: "phone_verification_required", phone_verified: true }, "phone_verification_required"],
] as const)("keeps canonical access policy authoritative and legacy admission safe (%j)", (proof, expected) => {
  const access: MaxAccess = { authenticated: true, email_verified: false, email_delivery_configured: true, phone_verified: false,
    phone_verification_required: false, phone_verification_available: false, can_create_project: false,
    reason: null, legal_document_version: "test", payments_configured: false, ...proof };
  expect(registrationVerificationReason(access)).toBe(expected);
});
