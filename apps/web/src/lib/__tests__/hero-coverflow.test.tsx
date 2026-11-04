import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { HeroStage } from "@/components/marketing/landing/HeroStage";

let root: Root;
let host: HTMLDivElement;
beforeEach(async () => {
  vi.useFakeTimers();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<HeroStage />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });

function slides() { return [...host.querySelectorAll<HTMLElement>("[data-scenario][data-position]")]; }
function active() { return host.querySelector<HTMLElement>('[data-position="active"]')!; }
function positions() { return Object.fromEntries(slides().map(slide => [slide.dataset.scenario, slide.dataset.position])); }
async function click(label: string) {
  const button = [...host.querySelectorAll<HTMLButtonElement>("button")].find(control => control.getAttribute("aria-label") === label || control.textContent === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
  return button!;
}

it("starts with cafe in front, salon on the right, shop on the left and club hidden", () => {
  expect(positions()).toEqual({ cafe: "active", salon: "next", club: "hidden", shop: "previous" });
  expect(active().textContent).toContain("Кофейня «Зёрна»");
  expect(active().textContent).toContain("9:56");
  expect(host.querySelector('[aria-label="Показать: Услуги"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="Показать: Магазин"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="Показать: Клуб"]')).toBeNull();
});

it("loops through all four scenarios in both directions and updates adjacent screens", async () => {
  for (const scenario of ["salon", "club", "shop", "cafe"]) {
    await click("Следующий экран");
    expect(active().dataset.scenario).toBe(scenario);
  }
  for (const scenario of ["shop", "club", "salon", "cafe"]) {
    await click("Предыдущий экран");
    expect(active().dataset.scenario).toBe(scenario);
  }
  await click("Следующий экран");
  expect(positions()).toEqual({ cafe: "previous", salon: "active", club: "next", shop: "hidden" });
});

it("selects a side phone without replacing phone nodes or losing focus on the persistent overlay", async () => {
  const original = slides();
  const side = host.querySelector<HTMLButtonElement>('[aria-label="Показать: Услуги"]')!;
  expect(side).not.toBeNull();
  side.focus();
  await act(async () => side.click());
  expect(active().dataset.scenario).toBe("salon");
  expect(document.activeElement).toBe(side);
  expect(side.getAttribute("aria-label")).toBe("Показать: Клуб");
  await act(async () => side.click());
  expect(active().dataset.scenario).toBe("club");
  slides().forEach((slide, index) => expect(slide).toBe(original[index]));
});

it("exposes only the active phone controls and keeps side selection outside inert content", async () => {
  const assertAccess = () => {
    expect(slides()).toHaveLength(4);
    for (const slide of slides()) {
      const content = slide.querySelector<HTMLElement>("[data-phone-content]")!;
      expect(content).not.toBeNull();
      const current = slide.dataset.position === "active";
      expect(content.hasAttribute("inert")).toBe(!current);
      expect(content.getAttribute("aria-hidden")).toBe(current ? null : "true");
      if (slide.dataset.position === "hidden") {
        expect(slide.hasAttribute("inert")).toBe(true);
        expect(slide.getAttribute("aria-hidden")).toBe("true");
      }
      for (const control of content.querySelectorAll("button")) expect(Boolean(control.closest("[inert]"))).toBe(!current);
    }
    const overlays = host.querySelectorAll<HTMLButtonElement>('button[aria-label^="Показать:"]');
    expect(overlays).toHaveLength(2);
    overlays.forEach(button => {
      expect(button.closest("[inert], [aria-hidden=true], [data-scenario], button button")).toBeNull();
      expect(document.getElementById(button.getAttribute("aria-controls")!)).not.toBeNull();
    });
  };
  assertAccess();
  await click("Показать: Услуги");
  assertAccess();
  expect(host.querySelector('button[aria-label="Клиент"], button[aria-label="Персонал"]')).toBeNull();
  expect(host.textContent).not.toMatch(/Персонал|Панель персонала|Клиент ·|Экран клиента/);
  expect([...host.querySelectorAll("button")].some(button => ["Клиент", "Персонал"].includes(button.textContent ?? ""))).toBe(false);
});

it("does not rotate the selected screen as time passes or intercept caret keys", async () => {
  const field = host.querySelector("textarea")!;
  field.focus();
  const move = new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
  await act(async () => field.dispatchEvent(move));
  expect(move.defaultPrevented).toBe(false);
  await act(async () => vi.advanceTimersByTime(10_000));
  expect(active().dataset.scenario).toBe("cafe");
  expect(document.activeElement).toBe(field);
});
