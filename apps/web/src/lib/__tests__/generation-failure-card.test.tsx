import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { GenerationFailureCard } from "@/components/workspace/GenerationFailureCard";

it("renders durable failure and prevents duplicate retry submits", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  let accept!: (value: boolean) => void;
  const retry = vi.fn(() => new Promise<boolean>((resolve) => { accept = resolve; }));
  await act(async () => root.render(createElement(GenerationFailureCard, {
    failure: { code: "deadline", message: "Не хватило времени на проверку", retryable: true }, onRetry: retry,
  })));
  expect(host.textContent).toContain("Не хватило времени на проверку");
  const button = host.querySelector("button")!;
  await act(async () => { button.click(); button.click(); });
  expect(retry).toHaveBeenCalledTimes(1);
  expect(button.disabled).toBe(true);
  await act(async () => accept(false));
  expect(button.disabled).toBe(false);
  await act(async () => root.unmount());
  host.remove();
});

it("does not offer retry for provider access or restoration errors", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => root.render(createElement(GenerationFailureCard, {
    failure: { code: "provider_access", message: "Требуется восстановить доступ", retryable: false }, onRetry: vi.fn(),
  })));
  expect(host.querySelector("button")).toBeNull();
  await act(async () => root.unmount());
});

it("handles a rejected retry without an unhandled rejection and permits another attempt", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => root.render(createElement(GenerationFailureCard, {
    failure: { code: "deadline", message: "Повторите запрос", retryable: true },
    onRetry: vi.fn().mockRejectedValue(new Error("network")),
  })));
  await act(async () => host.querySelector("button")!.click());
  expect(host.querySelector("button")!.disabled).toBe(false);
  expect(host.textContent).toContain("Не удалось запустить генерацию");
  await act(async () => root.unmount());
});
