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
    const values = { email: "owner@example.com", phone: "+79991234567", password: "moss river blue", confirm: "moss river blue" };
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

it("rejects a common long password locally and connects accessible help to the field", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(<MaxRegisterForm />));
    for (const [name, value] of Object.entries({ email: "owner@example.com", phone: "+79991234567", password: "passwordpassword", confirm: "passwordpassword" })) await act(async () => {
      const input = container.querySelector<HTMLInputElement>(`input[name=${name}]`)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    for (const box of container.querySelectorAll<HTMLInputElement>("input[type=checkbox][required]")) await act(async () => box.click());
    await act(async () => { container.querySelector("form")!.requestSubmit(); });
    const field = container.querySelector<HTMLInputElement>("#max-password")!;
    expect(field.minLength).toBe(15); expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(field.getAttribute("aria-describedby")).toContain("max-password-help");
    expect(container.querySelector("#max-password-help")?.textContent).toContain("цифры и спецсимволы необязательны");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("менее распространённый");
    expect(container.querySelector('[role="alert"]')?.textContent).not.toContain("passwordpassword");
    expect(document.activeElement).toBe(field); expect(mocks.action).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("keeps the cookie-updated registration page available to its client action state", async () => {
  const page = await MaxRegisterPage({});
  const html = renderToStaticMarkup(page);
  expect(html).toContain("Открываем аккаунт");
  const document = new DOMParser().parseFromString(html, "text/html");
  expect([...document.querySelectorAll("h1")].map(heading => heading.textContent)).toEqual(["Регистрация"]);
  expect(document.querySelector(".max-auth-card > p:not([role])")).toBeNull();
});

it("carries the staff join destination from registration back to login", async () => {
  const next = "/max/staff/join";
  const page = await MaxRegisterPage({ searchParams: Promise.resolve({ next: `${next}?token=discard` }) });
  const html = renderToStaticMarkup(page);
  const document = new DOMParser().parseFromString(html, "text/html");
  expect(document.querySelector('a[href^="/login"]')?.getAttribute("href")).toBe(`/login?next=${encodeURIComponent(next)}`);
  expect(html).not.toContain("discard");
});

async function registration(run: (container: HTMLDivElement) => Promise<void>) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(<MaxRegisterForm />));
    for (const [name, value] of Object.entries({ email: "example@example.test", phone: "+79990000000", password: "moss river blue", confirm: "moss river blue" })) await act(async () => {
      const input = container.querySelector<HTMLInputElement>(`input[name=${name}]`)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await run(container);
  } finally { await act(async () => root.unmount()); container.remove(); }
}

it("marks every mandatory signup label while keeping marketing optional and unmarked", async () => {
  await registration(async container => {
    expect(container.textContent).toContain("* — обязательные поля");
    const required = Array.from(container.querySelectorAll<HTMLInputElement>("input[required]"));
    expect(required).toHaveLength(7);
    for (const input of required) {
      expect(input.labels?.[0]?.textContent, input.name).toContain("*");
      expect(input.labels?.[0]?.querySelector('[aria-hidden="true"]')?.textContent, input.name).toBe("*");
    }
    const marketing = container.querySelector<HTMLInputElement>('input[name="marketing_accepted"]')!;
    expect(marketing.required).toBe(false);
    expect(marketing.labels?.[0]?.textContent).not.toContain("*");
    expect(mocks.action).not.toHaveBeenCalled();
  });
});

it("keeps the continue action without the three informational tiles below it", async () => {
  await registration(async container => {
    expect(Array.from(container.querySelectorAll("button")).some(button => button.textContent?.includes("Продолжить"))).toBe(true);
    for (const copy of ["Email — для входа и восстановления", "Секреты не передаются агенту", "MAX и платежи подключаются позже"]) {
      expect(container.textContent).not.toContain(copy);
    }
    expect(mocks.action).not.toHaveBeenCalled();
  });
});

it("toggles each password independently without submitting, replacing inputs or changing their form values", async () => {
  await registration(async container => {
    const password = container.querySelector<HTMLInputElement>('input[name="password"]')!;
    const confirm = container.querySelector<HTMLInputElement>('input[name="confirm"]')!;
    const toggle = async (label: string) => {
      const button = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
      expect(button, label).not.toBeNull();
      expect(button!.type).toBe("button");
      await act(async () => button!.click());
      return button!;
    };
    password.focus();
    expect(password.type).toBe("password"); expect(confirm.type).toBe("password");
    const first = await toggle("Показать пароль");
    expect(password.type).toBe("text"); expect(confirm.type).toBe("password");
    expect(document.activeElement).toBe(password);
    expect(first.getAttribute("aria-pressed")).toBe("true");
    await toggle("Показать повтор пароля");
    expect(confirm.type).toBe("text");
    first.focus();
    await toggle("Скрыть пароль");
    expect(password.type).toBe("password"); expect(confirm.type).toBe("text");
    expect(document.activeElement).toBe(first);
    expect(container.querySelector('input[name="password"]')).toBe(password);
    expect(container.querySelector('input[name="confirm"]')).toBe(confirm);
    for (const input of [password, confirm]) {
      expect(input.required).toBe(true); expect(input.minLength).toBe(15); expect(input.autocomplete).toBe("new-password");
      // Native maxLength counts UTF-16 units and would truncate a valid
      // 128-code-point Unicode password. The shared policy bounds it instead.
      expect(input.hasAttribute("maxlength")).toBe(false);
    }
    const data = new FormData(container.querySelector("form")!);
    expect(data.get("password")).toBe("moss river blue"); expect(data.get("confirm")).toBe("moss river blue");
    expect(mocks.action).not.toHaveBeenCalled();
  });
});

it.each(["terms_accepted", "privacy_accepted", "personal_data_accepted"])("localizes required %s and clears validation on check without making marketing required", async missing => {
  await registration(async container => {
    const form = container.querySelector("form")!;
    const box = container.querySelector<HTMLInputElement>(`input[name="${missing}"]`)!;
    for (const other of container.querySelectorAll<HTMLInputElement>('input[type="checkbox"][required]')) if (other !== box) await act(async () => other.click());
    await act(async () => form.requestSubmit());
    expect(mocks.action).not.toHaveBeenCalled();
    expect(box.validationMessage).toContain("галочку"); expect(box.validationMessage).not.toContain("флажок");
    expect(document.getElementById(box.getAttribute("aria-describedby")!)?.textContent).toContain("галочку");
    expect(box.labels?.[0].textContent).not.toContain("галочку");
    expect(box.getAttribute("aria-invalid")).toBe("true");
    await act(async () => box.click());
    expect(box.validationMessage).toBe(""); expect(box.getAttribute("aria-invalid")).toBeNull();
    await act(async () => box.click());
    await act(async () => form.requestSubmit());
    expect(box.validationMessage).toContain("галочку"); expect(mocks.action).not.toHaveBeenCalled();
    await act(async () => box.click());
    await act(async () => form.requestSubmit());
    expect(mocks.action).toHaveBeenCalledTimes(1);
    const data = mocks.action.mock.calls[0][1] as FormData;
    for (const name of ["terms_accepted", "privacy_accepted", "personal_data_accepted"]) expect(data.get(name)).toBe("on");
    expect(data.has("marketing_accepted")).toBe(false);
  });
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
    for (const [name, value] of Object.entries({ email: "owner@example.com", phone: "+79991234567", password: "moss river blue", confirm: "moss river blue" })) await act(async () => {
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
