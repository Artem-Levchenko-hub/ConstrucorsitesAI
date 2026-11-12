import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { YleumLanding } from "@/components/marketing/YleumLanding";

describe("Yleum landing conversion", () => {
  let host: HTMLDivElement;
  let root: Root;
  const events: unknown[] = [];
  const capture = (event: Event) => events.push((event as CustomEvent).detail);
  const now = Date.parse("2026-11-12T06:41:00Z");
  beforeEach(async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    events.length = 0;
    host = document.createElement("div"); document.body.append(host);
    root = createRoot(host);
    window.addEventListener("omnia:marketing", capture);
    await act(async () => root.render(<YleumLanding now={now} />));
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); window.removeEventListener("omnia:marketing", capture); });
  it("sends the signup placement without user data and keeps a real registration link", () => {
    const cta = host.querySelector<HTMLAnchorElement>('[data-marketing="signup_click"][data-placement="hero"]');
    expect(cta).not.toBeNull();
    expect(cta?.getAttribute("href")).toBe("/max/register");
    cta?.addEventListener("click", event => event.preventDefault(), { once: true });
    cta?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(events).toContainEqual({ event: "max_signup_click", page: "landing", placement: "hero" });
  });
  it("changes the scenario preview when a visitor selects services", async () => {
    const tab = Array.from(host.querySelectorAll('button')).find(el => el.textContent === "Услуги");
    expect(tab).toBeDefined();
    await act(async () => tab?.click());
    const pressed = Array.from(host.querySelectorAll("button")).find(el => el.textContent === "Услуги");
    expect(pressed?.getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector('[data-position="active"]')?.textContent).toContain("Студия красоты «Линия»");
    expect(events).toContainEqual({ event: "max_scenario_select", page: "landing", placement: "services" });
  });
  it("preserves independent cafe and shop carts while the coverflow loops", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const button = (name: string) => {
      const control = Array.from(example.querySelectorAll("button")).find(el => !el.closest("[inert]") && (el.textContent?.trim() === name || el.getAttribute("aria-label") === name));
      expect(control, `Missing control: ${name}`).toBeDefined();
      return control!;
    };
    await act(async () => button("Добавить Капучино")?.click());
    await act(async () => button("Выпечка").click());
    await act(async () => button("Добавить Круассан с миндалём").click());
    for (let index = 0; index < 3; index++) await act(async () => button("Следующий экран").click());
    await act(async () => button("Добавить Фикус").click());
    await act(async () => button("Следующий экран").click());
    const cafe = example.querySelector('[data-position="active"]');
    expect(cafe?.textContent).toContain("Корзина · 2");
    expect(cafe?.textContent).toContain("430 ₽");
    await act(async () => button("Предыдущий экран").click());
    const shop = example.querySelector('[data-position="active"]');
    expect(shop?.textContent).toContain("Корзина · 1");
    expect(shop?.textContent).toContain("980 ₽");
  });
  it("retains the selected appointment time after leaving and returning", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const click = async (name: string) => {
      const button = Array.from(example.querySelectorAll("button")).find(el => el.textContent === name);
      expect(button).toBeDefined();
      await act(async () => button?.click());
    };
    await click("Услуги");
    await click("17:30");
    await click("Клуб");
    await click("Услуги");
    const active = example.querySelector('[data-position="active"]');
    expect(active?.textContent).toContain("Сегодня в 10:00");
    expect(active?.querySelector('.ys-slots button[aria-pressed="true"]')?.textContent).toBe("17:30");
  });
  it("opens and confirms an earlier order from its date row in the feature examples", async () => {
    const examples = host.querySelector("#examples")!;
    const action = examples.querySelector<HTMLButtonElement>('button[aria-label="Повторить заказ от 24 сентября"]');
    expect(action?.closest("[inert]")).toBeNull();
    expect(action?.parentElement?.textContent).toContain("24 сентября");
    expect(examples.textContent).toContain("250 мл · 1 шт.");
    await act(async () => action?.click());
    expect(examples.textContent).toContain("Повтор заказа");
    expect(examples.textContent).toContain("Флэт уайт");
    expect(examples.textContent).toContain("260 ₽");
    const confirm = [...examples.querySelectorAll("button")].find(button => button.textContent === "Подтвердить повтор")!;
    await act(async () => confirm.click());
    expect(examples.textContent).toContain("Демо-заказ повторён");
    const back = [...examples.querySelectorAll("button")].find(button => button.textContent === "К заказам")!;
    await act(async () => back.click());
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Повторить заказ от 24 сентября");
  });
  it("keeps the same participant count after joining and switching scenarios", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const click = async (name: string) => {
      const button = Array.from(example.querySelectorAll("button")).find(el => el.textContent?.trim() === name);
      expect(button).toBeDefined();
      await act(async () => button?.click());
    };
    await click("Клуб");
    await click("Записаться на встречу");
    await click("Магазин");
    await click("Клуб");
    const active = example.querySelector('[data-position="active"]');
    expect(active?.textContent).toContain("15 участников");
    expect(active?.textContent).toContain("Вы записаны");
  });
  it("preserves an authored prompt and the shop selection across scenario changes", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const input = example.querySelector<HTMLTextAreaElement>("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "Приложение моей пекарни");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const click = async (name: string) => {
      const button = Array.from(example.querySelectorAll("button")).find(el => el.textContent === name || el.getAttribute("aria-label") === name);
      expect(button).toBeDefined();
      await act(async () => button?.click());
    };
    await click("Магазин");
    await click("Добавить Фикус");
    await click("Кофейня");
    expect(example.querySelector('[data-position="active"]')?.textContent).not.toContain("Фикус");
    await click("Магазин");
    const slide = example.querySelector('[data-position="active"]');
    expect(slide?.textContent).toContain("Фикус");
    expect(slide?.textContent).toContain("980");
    expect(slide?.textContent).not.toContain("Капучино");
    expect(example.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe("Приложение моей пекарни");
  });
  it("tracks the destination scenario for arrow and side-phone selection with legacy event names", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const click = async (name: string) => {
      const control = [...example.querySelectorAll<HTMLButtonElement>("button")].find(button => button.getAttribute("aria-label") === name || button.textContent === name);
      expect(control, name).toBeDefined();
      await act(async () => control!.click());
    };
    await click("Следующий экран");
    await click("Показать: Клуб");
    await click("Магазин");
    await click("Следующий экран");
    expect(events.filter(value => (value as { event?: string }).event === "max_scenario_select")).toEqual([
      { event: "max_scenario_select", page: "landing", placement: "services" },
      { event: "max_scenario_select", page: "landing", placement: "community" },
      { event: "max_scenario_select", page: "landing", placement: "shop" },
      { event: "max_scenario_select", page: "landing", placement: "coffee" },
    ]);
  });
  it("captures the requested scenario before a click handler updates the next destination", async () => {
    const next = host.querySelector<HTMLButtonElement>('.ys-coverflow-navigation [aria-label="Следующий экран"]')!;
    expect(next.dataset.placement).toBe("services");
    next.addEventListener("click", () => { next.dataset.placement = "community"; }, { once: true });
    await act(async () => next.click());
    expect(events.filter(value => (value as { event?: string }).event === "max_scenario_select")).toEqual([
      { event: "max_scenario_select", page: "landing", placement: "services" },
    ]);
  });
  it("describes generation and edits without promising customer version restoration", () => {
    expect(host.querySelector("#process")?.textContent).toContain("обычными словами");
    expect(host.textContent).not.toContain("Откат за секунды");
    expect(host.textContent).not.toContain("Вернитесь к предыдущей версии");
    expect(host.textContent).not.toContain("История версий");
  });
  it("keeps every section navigation target on the page", () => {
    for (const link of host.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
      expect(host.querySelector(link.getAttribute("href")!)).not.toBeNull();
    }
  });
});
