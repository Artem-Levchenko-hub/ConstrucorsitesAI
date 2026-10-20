import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { MaxOnboarding } from "@/components/max/MaxOnboarding";
import { acceptMaxDocuments, getMaxAccess, resendVerification, verifyEmailCode } from "@/lib/api/max-account";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api/max-account", () => ({
  getMaxAccess: vi.fn(),
  resendVerification: vi.fn(),
  verifyEmailCode: vi.fn(),
  acceptMaxDocuments: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let root: Root;
let container: HTMLDivElement;
let queryClient: QueryClient;

async function render(configured: boolean) {
  vi.mocked(getMaxAccess).mockResolvedValue({
    authenticated: true,
    email_verified: false,
    email_delivery_configured: configured,
    can_create_project: false,
    reason: "email_unverified",
    legal_document_version: "test",
    payments_configured: false,
  });
  await act(async () => {
    root.render(<QueryClientProvider client={queryClient}><MaxOnboarding email="owner@example.com" /></QueryClientProvider>);
  });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.mocked(resendVerification).mockResolvedValue({ accepted: true });
  vi.mocked(verifyEmailCode).mockResolvedValue({ verified: true });
  vi.mocked(acceptMaxDocuments).mockResolvedValue({ accepted: true });
});

afterEach(async () => {
  await act(async () => root.unmount());
  queryClient.clear();
  container.remove();
  vi.clearAllMocks();
});

it("hides code submission until email delivery is configured", async () => {
  await render(false);
  expect(container.querySelector("#email-verification-code")).toBeNull();
  expect(container.textContent).toContain("Отправка писем пока недоступна");
  expect(container.querySelector<HTMLButtonElement>("button")?.disabled).toBe(true);
});

it("submits six numeric digits and reports a rejected code", async () => {
  vi.mocked(verifyEmailCode).mockRejectedValue(new Error("Неверный код"));
  await render(true);
  const input = container.querySelector<HTMLInputElement>("#email-verification-code")!;
  expect(input.inputMode).toBe("numeric");
  expect(input.autocomplete).toBe("one-time-code");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, "12x3456");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(input.value).toBe("123456");
  await act(async () => {
    input.closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(vi.mocked(verifyEmailCode).mock.calls[0]?.[0]).toBe("123456");
  expect(container.querySelector("[role='alert']")?.textContent).toContain("Неверный код");
});

it("refreshes access and enters the studio after verification", async () => {
  await render(true);
  vi.mocked(getMaxAccess).mockResolvedValue({
    authenticated: true,
    email_verified: true,
    email_delivery_configured: true,
    can_create_project: true,
    reason: "ok",
    legal_document_version: "test",
    payments_configured: false,
  });
  const input = container.querySelector<HTMLInputElement>("#email-verification-code")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "123456");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    input.closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(getMaxAccess).toHaveBeenCalledTimes(2);
  expect(replace).toHaveBeenCalledWith("/max");
});

it("reports delivery failure without claiming that a letter was sent", async () => {
  vi.mocked(resendVerification).mockRejectedValue(new Error("SMTP unavailable"));
  await render(true);
  const resend = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("Отправить ещё раз"))!;
  await act(async () => { resend.click(); });
  expect(toast.error).toHaveBeenCalledWith("Не удалось отправить письмо", { description: "SMTP unavailable" });
  expect(toast.success).not.toHaveBeenCalled();
  expect(container.textContent).not.toContain("Письмо отправлено на");
});

it("requires explicit current consents even when the email is already verified", async () => {
  await render(true);
  vi.mocked(getMaxAccess).mockResolvedValue({
    authenticated: true, email_verified: true, email_delivery_configured: true,
    can_create_project: false, reason: "legal_acceptance_required",
    legal_document_version: "current-test-version", payments_configured: false,
  });
  await act(async () => { await queryClient.refetchQueries({ queryKey: ["max-access"] }); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  expect(replace).not.toHaveBeenCalled();
  const form = container.querySelector("form")!;
  const boxes = Array.from(form.querySelectorAll<HTMLInputElement>("input[type='checkbox']"));
  expect(boxes).toHaveLength(3);
  expect(boxes.every((box) => box.required && !box.checked)).toBe(true);
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(acceptMaxDocuments).not.toHaveBeenCalled();
  for (const box of boxes) await act(async () => { box.click(); });
  vi.mocked(getMaxAccess).mockResolvedValue({
    authenticated: true, email_verified: true, email_delivery_configured: true,
    can_create_project: true, reason: null,
    legal_document_version: "current-test-version", payments_configured: false,
  });
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(vi.mocked(acceptMaxDocuments).mock.calls[0]?.[0]).toEqual({
    terms_accepted: true, privacy_accepted: true, personal_data_accepted: true,
    document_version: "current-test-version",
  });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  expect(replace).toHaveBeenCalledWith("/max");
});
