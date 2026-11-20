import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, expect, it, vi } from "vitest";
import { GenerationFailureCard } from "@/components/workspace/GenerationFailureCard";

beforeEach(() => Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }));

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
  expect(host.querySelector('[role="alert"]')).toBeNull();
  const card = host.querySelector('[role="status"]')!;
  expect(card.className).not.toMatch(/danger|red-/);
  expect(card.textContent).toContain("Повторим запрос");
  expect(card.querySelector("button")?.textContent).toContain("Повторить запрос");
  expect(card.textContent).not.toContain("Генерация не завершена");
  const details = card.querySelector("details")!;
  expect(details.open).toBe(false);
  expect(details.textContent).toContain("Не хватило времени на проверку");
  expect(retry).not.toHaveBeenCalled();
  const button = host.querySelector("button")!;
  await act(async () => { button.click(); button.click(); });
  expect(retry).toHaveBeenCalledTimes(1);
  expect(button.disabled).toBe(true);
  await act(async () => accept(false));
  expect(button.disabled).toBe(false);
  await act(async () => root.unmount());
  host.remove();
});

it("keeps recovery-required failures out of the retry path and offers a return to the draft", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const retry = vi.fn();
  const returnToPrompt = vi.fn();
  await act(async () => root.render(createElement(GenerationFailureCard, {
    failure: { code: "runtime_unavailable", message: "Требуется восстановить среду", retryable: false },
    onRetry: retry, onReturnToPrompt: returnToPrompt,
  })));
  expect(host.textContent).toContain("Требуется восстановить среду");
  expect(host.querySelector("button")?.textContent).toContain("Вернуться к запросу");
  await act(async () => host.querySelector("button")!.click());
  expect(returnToPrompt).toHaveBeenCalledOnce();
  expect(retry).not.toHaveBeenCalled();
  await act(async () => root.unmount());
});

it.each(["provider_access", "provider_unavailable"])("allows only an explicit new request for %s without changing its retryable flag", async code => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const retry = vi.fn().mockResolvedValue(false);
  const failure = { code, message: "Провайдер отклонил запрос. Автоматический повтор остановлен.", retryable: false };
  await act(async () => root.render(<GenerationFailureCard failure={failure} onRetry={retry} />));
  expect(retry).not.toHaveBeenCalled();
  expect(host.querySelector('[role="status"]')?.classList.contains("generation-failure-card")).toBe(true);
  expect(host.querySelector("button")?.textContent).toContain("Повторить запрос");
  expect(host.textContent).toContain("Повтор отправляется как новый запрос");
  expect(host.textContent).toContain("лимит генераций или правок");
  await act(async () => host.querySelector("button")!.click());
  expect(retry).toHaveBeenCalledOnce();
  expect(failure.retryable).toBe(false);
  expect(host.textContent).toContain("Запрос пока не принят");
  expect(host.querySelector("button")!.disabled).toBe(false);
  await act(async () => root.unmount());
});

it("offers saved-draft continuation only when explicitly supplied by the caller", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const retry = vi.fn().mockResolvedValue(false);
  await act(async () => root.render(<GenerationFailureCard
    failure={{ code: "runtime_unavailable", message: "Проверка остановилась", retryable: false }}
    continueFromDraft onRetry={retry} />));
  expect(host.textContent).toContain("Продолжим с сохранённого черновика");
  expect(host.querySelector("button")?.textContent).toBe("Продолжить сборку");
  expect(retry).not.toHaveBeenCalled();
  await act(async () => host.querySelector("button")!.click());
  expect(retry).toHaveBeenCalledOnce();
  await act(async () => root.unmount());
});

it("does not create a provider retry action when the caller withholds admission", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const returnToPrompt = vi.fn();
  await act(async () => root.render(<GenerationFailureCard
    failure={{ code: "provider_unavailable", message: "Доступ ограничен", retryable: false }}
    onReturnToPrompt={returnToPrompt} />));
  expect(host.textContent).not.toContain("Продолжить сборку");
  expect(host.textContent).toContain("Вернуться к запросу");
  expect(returnToPrompt).not.toHaveBeenCalled();
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
  expect(host.textContent).toContain("Запрос пока не принят");
  await act(async () => root.unmount());
});
