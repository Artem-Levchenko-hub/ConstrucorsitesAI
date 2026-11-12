import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { HeroStage } from "@/components/marketing/landing/HeroStage";

let root: Root;
let host: HTMLDivElement;
const now = Date.parse("2026-11-12T10:00:00Z");
beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<HeroStage initialNow={now} />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });

function slides() { return [...host.querySelectorAll<HTMLElement>("[data-scenario][data-position]")]; }
function active() { return host.querySelector<HTMLElement>('[data-position="active"]')!; }
function positions() { return Object.fromEntries(slides().map(slide => [slide.dataset.scenario, slide.dataset.position])); }
async function click(label: string) {
  const button = [...host.querySelectorAll<HTMLButtonElement>("button")].find(control => !control.closest("[inert]") && (control.getAttribute("aria-label") === label || control.textContent?.trim() === label));
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

it("orders coffee, pastries, dessert and tea together across categories", async () => {
  await click("Добавить Капучино");
  await click("Выпечка");
  expect(active().textContent).not.toContain("Капучино");
  await click("Добавить Круассан с миндалём");
  await click("Десерты");
  await click("Добавить Чизкейк");
  await click("Чай");
  await click("Добавить Эрл Грей");
  await click("Открыть корзину");

  for (const item of ["Капучино", "Круассан с миндалём", "Чизкейк", "Эрл Грей"]) {
    expect(active().textContent).toContain(item);
  }
  expect(active().textContent).toContain("890 ₽");
  await click("Подтвердить заказ");
  expect(active().textContent).toContain("Демо-заказ оформлен");
  expect(active().textContent).toContain("Самовывоз к 9:56");
  expect(active().textContent).toContain("Код выдачи1482");
});

it("opens every plant category and keeps its cart separate from the café", async () => {
  await click("Добавить Капучино");
  await click("Показать: Магазин");
  await click("Неприхотливые");
  expect(active().textContent).toContain("Замиокулькас");
  expect(active().textContent).not.toContain("Калатея");
  await click("Крупные");
  expect(active().textContent).toContain("Монстера");
  expect(active().textContent).not.toContain("Замиокулькас");
  await click("Кашпо");
  await click("Добавить Кашпо «Песок»");
  await click("Открыть корзину");
  expect(active().textContent).toContain("650 ₽");
  expect(active().textContent).not.toContain("Капучино");
});

it("keeps keyboard focus in the cart when the focused line is removed", async () => {
  await click("Добавить Капучино");
  await click("Открыть корзину");
  const remove = active().querySelector<HTMLButtonElement>('[aria-label="Убрать Капучино"]')!;
  remove.focus();
  await act(async () => remove.click());
  expect(active().contains(document.activeElement)).toBe(true);
  expect(active().textContent).toContain("Корзина пуста");
  await click("Вернуться в каталог");
  expect(active().textContent).toContain("Капучино");
});

it("rejects a salon slot that expires before the next clock refresh", async () => {
  await click("Услуги");
  expect(active().textContent).toContain("Сегодня в 14:30");
  expect(active().querySelector('.ys-slots button[aria-pressed="true"]')?.textContent).toBe("14:30");
  vi.setSystemTime(Date.parse("2026-11-12T11:30:01Z"));
  await click("Оплатить");
  expect(active().textContent).not.toContain("Демо-запись оформлена");
  expect(active().textContent).toContain("Сегодня в 17:30");
  await click("17:30");
  await click("Оплатить");
  expect(active().textContent).toContain("Демо-запись оформлена");
});

it("does not move a joined club meeting to a different date when the clock advances", async () => {
  await click("Клуб");
  await click("Записаться на встречу");
  const date = () => active().querySelector(".ys-club-meeting time")?.getAttribute("datetime");
  expect(date()).toBe("2026-11-13");
  vi.setSystemTime(Date.parse("2026-11-12T21:01:00Z"));
  await act(async () => vi.advanceTimersByTime(30_000));
  expect(date()).toBe("2026-11-13");
  expect(active().textContent).toContain("Вы записаны");
});
