import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { DiscoveryChips } from "@/components/workspace/DiscoveryChips";

async function run(choices: string[], multiSelect: boolean, check: (host: HTMLDivElement, picked: ReturnType<typeof vi.fn>) => Promise<void>) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); const picked = vi.fn();
  await act(async () => root.render(<DiscoveryChips choices={choices} allowCustom multiSelect={multiSelect} onPick={picked} />));
  try { await check(host, picked); } finally { await act(async () => root.unmount()); host.remove(); }
}
function type(host: HTMLElement, text: string) {
  const input = host.querySelector("input")!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, text);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
it("keeps an empty-choice custom contract reachable and sends only on explicit action", async () => {
  await run([], false, async (host, picked) => {
    expect(host.querySelector("input")).not.toBeNull();
    await act(async () => type(host, "Мой собственный экран"));
    expect(picked).not.toHaveBeenCalled();
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Отправить ответ"]')!.click());
    expect(picked).toHaveBeenCalledExactlyOnceWith("Мой собственный экран");
  });
});
it("Done includes typed but uncommitted custom text beside selected choices", async () => {
  await run(["Каталог"], true, async (host, picked) => {
    await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Другое"))!.click());
    await act(async () => type(host, "Дневник"));
    expect(picked).not.toHaveBeenCalled();
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Готово"))!.click());
    expect(picked).toHaveBeenCalledExactlyOnceWith("Каталог, Дневник");
  });
});
it("a custom-only multi answer can be submitted with Done without an extra plus", async () => {
  await run([], true, async (host, picked) => {
    await act(async () => type(host, "Красные кнопки"));
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Готово"))!.click());
    expect(picked).toHaveBeenCalledExactlyOnceWith("Красные кнопки");
  });
});

it("checks the complete multi-answer budget without truncating or invoking the parent", async () => {
  await run(["Каталог"], true, async (host, picked) => {
    await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Другое"))!.click());
    const prefix = "Каталог, "; const boundary = "😀".repeat(30_000 - Array.from(prefix).length);
    await act(async () => type(host, boundary + "я"));
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Готово"))!.click());
    expect(picked).not.toHaveBeenCalled(); expect(host.querySelector("input")?.value).toBe(boundary + "я");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("30000");
    await act(async () => type(host, boundary));
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Готово"))!.click());
    expect(picked).toHaveBeenCalledExactlyOnceWith(prefix + boundary);
  });
});
