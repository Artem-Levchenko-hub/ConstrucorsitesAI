import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { MaxOnboarding } from "@/components/max/MaxOnboarding";
import { VerifyMaxEmail } from "@/components/max/VerifyMaxEmail";
import { EmailVerification } from "@/components/max/EmailVerification";
import { verifyEmailCode } from "@/lib/api/max-account";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("retains the email fallback code after rejection and sends only six digits to the existing endpoint", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => Response.json({ error: { code: "code_invalid", message: "Неверный код" } }, { status: 400 }));
  vi.stubGlobal("fetch", fetcher);
  const verified = vi.fn(); const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(<EmailVerification email="owner@example.com" available onVerified={verified} />));
    const input = container.querySelector<HTMLInputElement>("#email-verification-code")!;
    expect(input.autocomplete).toBe("one-time-code"); expect(input.inputMode).toBe("numeric");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "12x3456");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(fetcher.mock.calls[0]).toEqual(["/api/auth/email/verify/code", expect.objectContaining({ body: '{"code":"123456"}' })]);
    expect(input.value).toBe("123456"); expect(container.querySelector("[role=alert]")?.textContent).toContain("Неверный код");
    expect(verified).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); }
});

it("does not claim an email was sent when fallback delivery fails", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: { code: "email_delivery_unavailable", message: "Почта временно недоступна" } }, { status: 503 })));
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(<EmailVerification email="owner@example.com" available onVerified={() => {}} />));
    await act(async () => { Array.from(container.querySelectorAll("button")).find(button => button.type === "button")!.click(); });
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Почта временно недоступна");
    expect(container.textContent).not.toContain("Письмо отправлено на");
  } finally { await act(async () => root.unmount()); }
});

it("keeps the saved account visible while fallback email delivery is unavailable", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(<EmailVerification email="owner@example.com" available={false} onVerified={() => {}} />));
    expect(container.querySelector("#email-verification-code")).toBeNull();
    expect(container.querySelector("[role=status]")?.textContent).toContain("Аккаунт сохранён");
    expect(container.querySelector<HTMLButtonElement>("button")?.disabled).toBe(true);
  } finally { await act(async () => root.unmount()); }
});

it("refreshes legacy email admission after a successful code without sending SMS", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let verified = false; const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    paths.push(path);
    if (path === "/api/max/account/access") return Response.json({ authenticated: true, email_verified: verified, email_delivery_configured: true,
      phone_verified: false, phone_verification_required: false, phone_verification_available: false,
      registration_verification_reason: verified ? null : "email_verification_required", can_create_project: verified,
      reason: verified ? null : "email_unverified", legal_document_version: "test", payments_configured: false });
    if (path === "/api/auth/email/verify/code") { verified = true; return Response.json({ verified: true }); }
    throw new Error(`Unexpected request: ${path}`);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxOnboarding email="owner@example.com" /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    const input = container.querySelector<HTMLInputElement>("#email-verification-code")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "123456");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(replace).toHaveBeenCalledWith("/max");
    expect(paths).toEqual(["/api/max/account/access", "/api/auth/email/verify/code", "/api/max/account/access"]);
  } finally { await act(async () => root.unmount()); client.clear(); }
});

it("keeps existing email-code links compatible without making them an access gate", async () => {
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => Response.json({ verified: true }));
  vi.stubGlobal("fetch", fetcher);
  expect(await verifyEmailCode("123456")).toEqual({ verified: true });
  expect(fetcher.mock.calls[0]).toEqual(["/api/auth/email/verify/code", expect.objectContaining({ credentials: "include", method: "POST", body: '{"code":"123456"}' })]);
});

it("keeps an optional email verification link working", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => Response.json({ verified: true })); vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(<VerifyMaxEmail token="optional-email-token" />));
    expect(container.textContent).toContain("Email подтверждён");
    expect(fetcher.mock.calls[0]).toEqual(["/api/auth/email/verify", expect.objectContaining({ method: "POST", body: '{"token":"optional-email-token"}' })]);
  } finally { await act(async () => root.unmount()); }
});

it("requires all current legal consents after phone confirmation even when email is unverified", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let accepted = false;
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/max/account/access") return Response.json({
      authenticated: true, email_verified: false, email_delivery_configured: false,
      phone_verified: true, phone_verification_required: false, phone_verification_available: true,
      can_create_project: accepted, reason: accepted ? null : "legal_acceptance_required",
      legal_document_version: "current-test-version", payments_configured: false,
    });
    if (path === "/api/max/account/acceptances") {
      expect(JSON.parse(String(init?.body))).toEqual({ terms_accepted: true, privacy_accepted: true, personal_data_accepted: true, document_version: "current-test-version" });
      accepted = true; return Response.json({ accepted: true });
    }
    throw new Error(`Unexpected request: ${path}`);
  }); vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MaxOnboarding email="owner@example.com" next="/billing/plan" /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    const form = container.querySelector("form")!;
    const boxes = Array.from(form.querySelectorAll<HTMLInputElement>("input[type=checkbox]"));
    expect(boxes).toHaveLength(3); expect(boxes.every(box => box.required && !box.checked)).toBe(true);
    await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(accepted).toBe(false); expect(replace).not.toHaveBeenCalled();
    for (const box of boxes) await act(async () => { box.click(); });
    await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(accepted).toBe(true); expect(replace).toHaveBeenCalledWith("/billing/plan");
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});
