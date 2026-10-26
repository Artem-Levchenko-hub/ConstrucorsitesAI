import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import MaxRegisterPage from "@/app/(auth)/max/register/page";

const mocks = vi.hoisted(() => ({ action: vi.fn(), replace: vi.fn() }));
vi.mock("@/app/(auth)/actions", () => ({ maxRegisterAction: mocks.action }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }), redirect: (path: string) => { throw new Error(`redirect:${path}`); } }));
vi.mock("@/lib/auth-mock", () => ({ getSession: async () => ({ email: "owner@example.com", isAnon: false }) }));
vi.mock("@/lib/oauth-login-server", () => ({ listOAuthProviders: async () => [] }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
beforeEach(() => { mocks.action.mockResolvedValue({ error: "Телефон уже зарегистрирован" }); });
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("preserves credentials, phone and chosen legal consents when signup is rejected", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(<MaxRegisterForm />));
    const values = { email: "owner@example.com", phone: "+79991234567", password: "secret123", confirm: "secret123" };
    for (const [name, value] of Object.entries(values)) await act(async () => {
      const input = container.querySelector<HTMLInputElement>(`input[name=${name}]`)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const boxes = Array.from(container.querySelectorAll<HTMLInputElement>("input[type=checkbox]"));
    for (const box of boxes) await act(async () => box.click());
    expect(boxes.map(box => [box.name, box.checked])).toEqual([
      ["terms_accepted", true], ["privacy_accepted", true], ["personal_data_accepted", true], ["marketing_accepted", true],
    ]);
    await act(async () => { container.querySelector("form")!.requestSubmit(); });
    expect(container.querySelector("[role=alert]")?.textContent).toContain("Телефон уже зарегистрирован");
    for (const [name, value] of Object.entries(values)) expect(container.querySelector<HTMLInputElement>(`input[name=${name}]`)?.value).toBe(value);
    expect(Array.from(container.querySelectorAll<HTMLInputElement>("input[type=checkbox]")).map(box => [box.name, box.checked])).toEqual([
      ["terms_accepted", true], ["privacy_accepted", true], ["personal_data_accepted", true], ["marketing_accepted", true],
    ]);
    expect(container.querySelector<HTMLInputElement>('input[name=phone]')?.autocomplete).toBe("tel");
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("keeps the cookie-updated registration page available to its client action state", async () => {
  const page = await MaxRegisterPage();
  expect(renderToStaticMarkup(page)).toContain("Открываем аккаунт");
});

it("routes a previously signed-in account through its existing onboarding flow", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); const root = createRoot(container);
  try {
    await act(async () => root.render(<MaxRegisterForm existingSession next="/billing/plan" />));
    expect(mocks.replace).toHaveBeenCalledWith("/max/onboarding?next=%2Fbilling%2Fplan");
    expect(container.querySelector("form")).toBeNull();
  } finally { await act(async () => root.unmount()); }
});

it("retains the newly registered phone when cookie mutation re-renders server props", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let finish!: (value: unknown) => void;
  mocks.action.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ authenticated: true, email_verified: false, email_delivery_configured: true,
    phone_verified: false, phone_verification_required: true, phone_verification_available: true,
    registration_verification_reason: "phone_verification_required", can_create_project: false,
    reason: "phone_verification_required", legal_document_version: "2026-10-26", payments_configured: false })));
  const client = new QueryClient(); const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const render = (existingSession: boolean) => <QueryClientProvider client={client}><MaxRegisterForm existingSession={existingSession}><span>Другой способ входа</span></MaxRegisterForm></QueryClientProvider>;
  try {
    await act(async () => root.render(render(false)));
    for (const [name, value] of Object.entries({ email: "owner@example.com", phone: "+79991234567", password: "secret123", confirm: "secret123" })) await act(async () => {
      const input = container.querySelector<HTMLInputElement>(`input[name=${name}]`)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    for (const box of container.querySelectorAll<HTMLInputElement>("input[type=checkbox][required]")) await act(async () => box.click());
    await act(async () => { container.querySelector("form")!.requestSubmit(); });
    await act(async () => root.render(render(true)));
    expect(mocks.replace).not.toHaveBeenCalled();
    await act(async () => { finish({ error: null, registered: { email: "owner@example.com", phone: "+79991234567", next: "/max" } }); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    await act(async () => root.render(render(true)));
    expect(container.querySelector<HTMLInputElement>("#verification-phone")?.value).toBe("+79991234567");
    expect(container.textContent).not.toContain("Другой способ входа");
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});
