import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { Providers } from "@/app/providers";

let root: Root;
let container: HTMLDivElement;
async function tick(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root.render(<Providers><div>Workspace</div></Providers>); });
});
afterEach(async () => {
  await act(async () => { toast.dismiss(); });
  await tick(500);
  await act(async () => { root.unmount(); });
  container.remove(); vi.useRealTimers(); vi.unstubAllGlobals();
});

it("keeps errors until dismissed while ordinary notices last eight seconds", async () => {
  await act(async () => { toast.error("Сбой сохранения", { duration: 1000 }); });
  await tick(20);
  await act(async () => { toast.success("Сохранено"); });
  await tick(20);
  const host = container.querySelector("[data-sonner-toaster]")!;
  expect(host.getAttribute("data-theme")).toBe("light");
  expect(host.getAttribute("data-y-position")).toBe("top");
  expect(host.getAttribute("data-x-position")).toBe("right");
  await tick(5000);
  expect(container.textContent).toContain("Сохранено");
  expect(container.textContent).toContain("Сбой сохранения");
  await tick(3500);
  expect(container.textContent).not.toContain("Сохранено");
  expect(container.textContent).toContain("Сбой сохранения");
  await act(async () => { container.querySelector<HTMLButtonElement>('[data-type="error"] [data-close-button]')!.click(); });
  await tick(500);
  expect(container.textContent).not.toContain("Сбой сохранения");
});

it("caps visible notices at three and keeps dismiss controls keyboard reachable", async () => {
  await act(async () => { for (let index = 0; index < 4; index++) toast.info(`Уведомление ${index}`); });
  await tick(20);
  expect(container.querySelectorAll('[data-sonner-toast][data-visible="true"]')).toHaveLength(3);
  const close = container.querySelector<HTMLButtonElement>('[data-visible="true"] [data-close-button]')!;
  expect(close).toBeTruthy();
  await act(async () => { close.focus(); });
  expect(document.activeElement).toBe(close);
});

it("lets a resolved error notice with the same ID expire normally", async () => {
  await act(async () => { toast.error("Сбой", { id: "retry-notice" }); });
  await tick(20);
  await act(async () => { toast.success("Повтор завершён", { id: "retry-notice" }); });
  await tick(20);
  expect(container.textContent).toContain("Повтор завершён");
  expect(container.textContent).not.toContain("Сбой");
  await tick(8500);
  expect(container.textContent).not.toContain("Повтор завершён");
});
