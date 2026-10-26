import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxOnboarding } from "@/components/max/MaxOnboarding";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let verified = false;
let available = true;
let deliveryError = false;
let invalidCode = false;
let pendingDelivery: Promise<Response> | null;
let legacyEmail = false;
let requests: { path: string; body: unknown }[];
const access = () => ({ authenticated: true, email_verified: false, email_delivery_configured: false,
  phone_verified: verified, phone_verification_required: !verified, phone_verification_available: available,
  registration_verification_reason: legacyEmail ? "email_verification_required" : verified ? null : "phone_verification_required",
  can_create_project: verified, reason: verified ? null : "phone_verification_required",
  legal_document_version: "test", payments_configured: false });

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  verified = false; available = true; deliveryError = false; invalidCode = false; pendingDelivery = null; legacyEmail = false; requests = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/max/account/access") return Response.json(access());
    requests.push({ path, body: JSON.parse(String(init?.body)) });
    expect(init?.credentials).toBe("include");
    if (path === "/api/auth/phone/request") {
      if (pendingDelivery) return pendingDelivery;
      return deliveryError
        ? Response.json({ error: { code: "phone_delivery_unavailable", message: "SMS временно недоступны" } }, { status: 503 })
        : Response.json({ accepted: true, expires_in: 600, resend_after: 60 }, { status: 202 });
    }
    if (path === "/api/auth/phone/verify") {
      if (invalidCode) return Response.json({ error: { code: "code_invalid", message: "Неверный код" } }, { status: 400 });
      verified = true; return Response.json({ verified: true });
    }
    throw new Error(`Unexpected request: ${path}`);
  }));
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals(); vi.clearAllMocks();
});
async function render() {
  await act(async () => { root.render(<QueryClientProvider client={client}><MaxOnboarding email="owner@example.com" /></QueryClientProvider>); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
async function change(selector: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(selector)!;
  expect(input).not.toBeNull();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit(selector: string) {
  await act(async () => { container.querySelector(selector)!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}

it("confirms a phone once and enters studio without requiring email confirmation", async () => {
  await render();
  await change("input[type=tel]", "+7 999 123-45-67");
  expect(requests).toHaveLength(0);
  await submit("form[data-phone-request]");
  expect(requests[0]).toEqual({ path: "/api/auth/phone/request", body: { phone: "+7 999 123-45-67" } });
  expect(container.textContent).toContain("Запрос на SMS принят");
  expect(container.querySelector<HTMLInputElement>("#phone-verification-code")?.autocomplete).toBe("one-time-code");
  await change("#phone-verification-code", "123456");
  await submit("form[data-phone-verify]");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(requests[1]).toEqual({ path: "/api/auth/phone/verify", body: { code: "123456" } });
  expect(replace).toHaveBeenCalledWith("/max");
});

it("keeps phone and code on failure and does not claim SMS delivery", async () => {
  deliveryError = true; await render(); await change("input[type=tel]", "+79991234567");
  await submit("form[data-phone-request]");
  expect(container.querySelector<HTMLInputElement>("input[type=tel]")?.value).toBe("+79991234567");
  expect(container.querySelector("[role=alert]")?.textContent).toContain("SMS временно недоступны");
  expect(container.textContent).not.toContain("Запрос на SMS принят");
  expect(container.querySelector<HTMLInputElement>("#phone-verification-code")?.disabled).toBe(false);
  deliveryError = false; await submit("form[data-phone-request]");
  invalidCode = true; await change("#phone-verification-code", "123456"); await submit("form[data-phone-verify]");
  expect(container.querySelector<HTMLInputElement>("#phone-verification-code")?.value).toBe("123456");
  expect(container.querySelector("[role=alert]")?.textContent).toContain("Неверный код");
  expect(replace).not.toHaveBeenCalled();
});

it("verifies an existing code after ambiguous SMS delivery failure without another SMS request", async () => {
  deliveryError = true; await render(); await change("input[type=tel]", "+79991234567");
  await submit("form[data-phone-request]");
  expect(container.querySelector("[role=alert]")?.textContent).toContain("SMS временно недоступны");
  expect(container.textContent).not.toContain("Запрос на SMS принят");
  await change("#phone-verification-code", "123456");
  await submit("form[data-phone-verify]");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(requests).toEqual([
    { path: "/api/auth/phone/request", body: { phone: "+79991234567" } },
    { path: "/api/auth/phone/verify", body: { code: "123456" } },
  ]);
  expect(replace).toHaveBeenCalledWith("/max");
});

it("verifies an existing SMS code on a fresh mount without a phone value or a new SMS request", async () => {
  available = false; await render();
  expect(container.querySelector<HTMLInputElement>("input[type=tel]")?.value).toBe("");
  expect(container.textContent).not.toContain("Запрос на SMS принят");
  await change("#phone-verification-code", "123456");
  await submit("form[data-phone-verify]");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(requests).toEqual([{ path: "/api/auth/phone/verify", body: { code: "123456" } }]);
  expect(replace).toHaveBeenCalledWith("/max");
});

it("prevents resend during the 60-second cooldown", async () => {
  await render(); await change("input[type=tel]", "+79991234567"); await submit("form[data-phone-request]");
  const resend = container.querySelector<HTMLButtonElement>("form[data-phone-request] button")!;
  expect(resend.disabled).toBe(true);
  expect(resend.textContent).toContain("60");
  await submit("form[data-phone-request]");
  expect(requests).toHaveLength(1);
});

it("waits for confirmed acceptance and admits only one pending SMS request", async () => {
  let accept!: (response: Response) => void;
  pendingDelivery = new Promise(resolve => { accept = resolve; });
  await render(); await change("input[type=tel]", "+79991234567");
  await act(async () => {
    const form = container.querySelector("form[data-phone-request]")!;
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(requests).toHaveLength(1);
  expect(container.textContent).not.toContain("Запрос на SMS принят");
  expect(container.querySelector<HTMLInputElement>("#phone-verification-code")?.disabled).toBe(true);
  expect(container.querySelector<HTMLButtonElement>("form[data-phone-request] button")?.disabled).toBe(true);
  await act(async () => { accept(Response.json({ accepted: true, expires_in: 600, resend_after: 60 }, { status: 202 })); });
  expect(container.textContent).toContain("Запрос на SMS принят");
});

it("validates six numeric digits before sending the phone code", async () => {
  await render(); await change("input[type=tel]", "+79991234567"); await submit("form[data-phone-request]");
  await change("#phone-verification-code", "12x3"); await submit("form[data-phone-verify]");
  expect(container.querySelector<HTMLInputElement>("#phone-verification-code")?.value).toBe("123");
  expect(container.querySelector("[role=alert]")?.textContent).toContain("6 цифр");
  expect(requests).toHaveLength(1);
});

it("offers an honest retry state when mandatory SMS delivery is unavailable", async () => {
  available = false; await render();
  expect(container.textContent).toContain("SMS временно недоступны");
  expect(container.querySelector<HTMLButtonElement>("form[data-phone-request] button")?.disabled).toBe(true);
  expect(requests).toHaveLength(0); expect(replace).not.toHaveBeenCalled();
});

it("does not ask a phone-verified user for another SMS or email proof", async () => {
  verified = true; await render();
  expect(container.querySelector("input[type=tel]")).toBeNull();
  expect(requests).toHaveLength(0); expect(replace).toHaveBeenCalledWith("/max");
});

it("keeps legacy email code confirmation available until the phone rollout is enabled", async () => {
  legacyEmail = true;
  // The provider is not activated; admission still needs the legacy email proof.
  const fetcher = vi.mocked(fetch);
  fetcher.mockImplementation(async (path) => {
    if (path === "/api/max/account/access") return Response.json({ ...access(), phone_verification_required: false, phone_verified: false, email_delivery_configured: true, can_create_project: false, reason: "email_unverified" });
    throw new Error(`Unexpected request: ${path}`);
  });
  await render();
  expect(container.querySelector("#email-verification-code")).not.toBeNull();
  expect(container.querySelector("input[type=tel]")).toBeNull();
  expect(replace).not.toHaveBeenCalled();
});

it("uses the canonical server reason for the confirmation UI", async () => {
  vi.mocked(fetch).mockImplementation(async (path) => {
    if (path === "/api/max/account/access") return Response.json({ ...access(), phone_verification_required: false, registration_verification_reason: "phone_verification_required" });
    throw new Error(`Unexpected request: ${path}`);
  });
  await render();
  expect(container.querySelector("input[type=tel]")).not.toBeNull();
  expect(container.querySelector("#email-verification-code")).toBeNull();
});

it("collects current legal acceptances before exposing the phone SMS request", async () => {
  let accepted = false;
  vi.mocked(fetch).mockImplementation(async (path, init) => {
    if (path === "/api/max/account/access") return Response.json({ ...access(), reason: accepted ? "phone_verification_required" : "legal_acceptance_required", legal_document_version: "2026-10-26" });
    if (path === "/api/max/account/acceptances") {
      expect(JSON.parse(String(init?.body))).toEqual({ terms_accepted: true, privacy_accepted: true, personal_data_accepted: true, document_version: "2026-10-26" });
      accepted = true; return Response.json({ accepted: true });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await render();
  expect(container.querySelector("input[type=tel]")).toBeNull();
  expect(container.querySelector("#phone-verification-code")).toBeNull();
  const boxes = Array.from(container.querySelectorAll<HTMLInputElement>("input[type=checkbox]"));
  expect(boxes).toHaveLength(3);
  for (const box of boxes) await act(async () => box.click());
  await submit("form");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(container.querySelector("input[type=tel]")).not.toBeNull();
  expect(requests).toHaveLength(0);
});
