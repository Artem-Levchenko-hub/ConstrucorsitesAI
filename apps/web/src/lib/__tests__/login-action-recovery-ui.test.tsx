import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LoginForm } from "@/components/auth/LoginForm";
import { loginAction } from "@/app/(auth)/actions";

vi.mock("@/app/(auth)/actions", () => ({ loginAction: vi.fn() }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("a stale login action leaves a recoverable form instead of a client exception", async () => {
  vi.mocked(loginAction).mockRejectedValue(Object.assign(new Error('Server Action "previous-version" was not found on the server.'), { name: "UnrecognizedActionError" }));
  await act(async () => root.render(<LoginForm next="/max/qa-project" />));
  const form = container.querySelector("form")!;
  (form.elements.namedItem("email") as HTMLInputElement).value = "qa@example.com";
  (form.elements.namedItem("password") as HTMLInputElement).value = "synthetic123";
  await act(async () => form.requestSubmit());
  expect(loginAction).toHaveBeenCalledTimes(1);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Обновите её");
  expect(container.querySelector('button[type="submit"]')?.hasAttribute("disabled")).toBe(true);
  const recovery = [...container.querySelectorAll("button")].find(button => button.textContent === "Обновить страницу");
  expect(recovery?.type).toBe("button");
  expect(recovery?.disabled).toBe(false);
  expect(container.querySelector('input[name="next"]')?.getAttribute("value")).toBe("/max/qa-project");
});
