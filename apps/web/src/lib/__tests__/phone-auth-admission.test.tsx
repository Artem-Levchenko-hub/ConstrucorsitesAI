import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { maxRegisterAction, loginAction } from "@/app/(auth)/actions";
import MaxStudioPage from "@/app/(app)/max/page";
import AppLayout from "@/app/(app)/layout";

const state = vi.hoisted(() => ({ session: { id: "owner", email: "owner@example.com", isAnon: false, emailVerifiedAt: null as string | null, phoneVerifiedAt: "2026-10-27T10:00:00Z" as string | null, phoneVerificationRequired: false, registrationVerificationReason: null as string | null, status: "active" }, target: "/max" }));
const cookieSet = vi.hoisted(() => vi.fn());
vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined, set: cookieSet }), headers: async () => new Headers({ "x-omnia-return-to": state.target }) }));
vi.mock("next/navigation", () => ({ redirect: (path: string) => { throw new Error(`redirect:${path}`); } }));
vi.mock("@/lib/auth-mock", () => ({ getSession: async () => state.session, getMaxAccessServer: async () => ({ can_create_project: true }) }));
vi.mock("@/components/max/MaxStudio", () => ({ MaxStudio: () => null }));

function registration(phone = "+79991234567", next = "/max") {
  const data = new FormData();
  for (const [key, value] of Object.entries({ email: "owner@example.com", phone, password: "secret123", confirm: "secret123", terms_accepted: "on", privacy_accepted: "on", personal_data_accepted: "on", next })) data.set(key, value);
  return data;
}
beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION", "");
  state.session.phoneVerificationRequired = false; state.session.registrationVerificationReason = null; state.session.phoneVerifiedAt = "2026-10-27T10:00:00Z"; state.session.emailVerifiedAt = null; state.target = "/max";
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { headers: { "set-cookie": "omnia_session=durable-session; Path=/; HttpOnly" } })));
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("retains phone and recovery email after durable signup without replaying signup for SMS", async () => {
  const result = await maxRegisterAction({ error: null }, registration("+7 999 123-45-67", "/billing/plan"));
  expect(result).toEqual({ error: null, registered: { phone: "+7 999 123-45-67", email: "owner@example.com", next: "/billing/plan" } });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body))).toMatchObject({ phone: "+7 999 123-45-67", email: "owner@example.com", product: "max", terms_accepted: true, privacy_accepted: true, personal_data_accepted: true, document_version: "2026-10-27" });
  expect(cookieSet).toHaveBeenCalledWith(expect.objectContaining({ name: "omnia_session", value: "durable-session", httpOnly: true, secure: true, sameSite: "lax" }));
});
it("rejects missing phone before signup", async () => {
  expect(await maxRegisterAction({ error: null }, registration(""))).toEqual({ error: "Введите телефон с кодом страны" });
  expect(fetch).not.toHaveBeenCalled();
});
it("sanitizes the post-confirmation return destination", async () => {
  expect(await maxRegisterAction({ error: null }, registration("+79991234567", "//evil.example"))).toMatchObject({ registered: { next: "/max" } });
});
it("opens studio for a phone-verified account without email confirmation", async () => {
  expect(await MaxStudioPage()).not.toBeNull();
});
it("sends a phone-unverified account to its one-time confirmation", async () => {
  state.session.phoneVerificationRequired = true;
  state.session.registrationVerificationReason = "phone_verification_required";
  state.session.phoneVerifiedAt = null;
  state.session.emailVerifiedAt = "2026-10-01T10:00:00Z";
  await expect(MaxStudioPage()).rejects.toThrow("redirect:/max/onboarding");
});
it("preserves email proof admission while phone rollout is disabled and neither identity is verified", async () => {
  state.session.phoneVerifiedAt = null;
  state.session.registrationVerificationReason = "email_verification_required";
  await expect(MaxStudioPage()).rejects.toThrow("redirect:/max/onboarding");
});
it("preserves a billing return through required phone confirmation", async () => {
  state.session.phoneVerificationRequired = true; state.session.registrationVerificationReason = "phone_verification_required"; state.target = "/billing/plan?payment=pay-42";
  await expect(AppLayout({ children: null })).rejects.toThrow("redirect:/max/onboarding?next=%2Fbilling%2Fplan%3Fpayment%3Dpay-42");
});
it("keeps routine login on email and password only", async () => {
  const data = registration();
  await expect(loginAction({ error: null }, data)).rejects.toThrow("redirect:/max");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toContain("/api/auth/login");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body))).toEqual({ email: "owner@example.com", password: "secret123" });
});
