import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { PasswordRecoveryForm } from "@/components/auth/PasswordRecoveryForm";
import { PasswordResetForm } from "@/components/auth/PasswordResetForm";

afterEach(() => vi.unstubAllGlobals());

it("keeps password recovery on email and reset links without requesting SMS", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const calls: { path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, body: JSON.parse(String(init?.body)) });
    return Response.json({ accepted: true });
  }));
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  async function change(selector: string, value: string) {
    await act(async () => {
      const input = container.querySelector<HTMLInputElement>(selector)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  async function submit() { await act(async () => { container.querySelector("form")!.requestSubmit(); }); }
  try {
    await act(async () => root.render(<PasswordRecoveryForm />));
    await change("input[type=email]", "owner@example.com"); await submit();
    expect(container.querySelector("[role=status]")?.textContent).toContain("письмо");
    await act(async () => root.render(<PasswordResetForm token="email-recovery-token" />));
    await change("#reset-password", "newpassword123"); await change("#reset-confirm", "newpassword123"); await submit();
    expect(container.textContent).toContain("Пароль изменён");
    expect(container.querySelector('a[href="/login"]')).not.toBeNull();
    expect(calls).toEqual([
      { path: "/api/auth/password/forgot", body: { email: "owner@example.com" } },
      { path: "/api/auth/password/reset", body: { token: "email-recovery-token", password: "newpassword123" } },
    ]);
  } finally { await act(async () => root.unmount()); container.remove(); }
});
