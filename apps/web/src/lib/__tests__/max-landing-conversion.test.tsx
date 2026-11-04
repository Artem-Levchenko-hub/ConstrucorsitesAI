import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { YleumLanding } from "@/components/marketing/YleumLanding";

describe("Yleum landing conversion", () => {
  let host: HTMLDivElement;
  let root: Root;
  const events: unknown[] = [];
  const capture = (event: Event) => events.push((event as CustomEvent).detail);
  beforeEach(async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    events.length = 0;
    host = document.createElement("div"); document.body.append(host);
    root = createRoot(host);
    window.addEventListener("omnia:marketing", capture);
    await act(async () => root.render(<YleumLanding />));
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
    // Выбор сценария пересоздаёт витрину (у неё новый key), поэтому кнопку
    // ищем заново: прежняя ссылка указывает на уже удалённый узел.
    const pressed = Array.from(host.querySelectorAll("button")).find(el => el.textContent === "Услуги");
    expect(pressed?.getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector('[data-testid="landing-example"]')?.textContent).toContain("Записаться на");
    expect(events).toContainEqual({ event: "max_scenario_select", page: "landing", placement: "services" });
  });
  it("shows the staff view of the same order and preserves the client cart", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const button = (name: string) => Array.from(example.querySelectorAll("button")).find(el => el.textContent === name || el.getAttribute("aria-label") === name);
    await act(async () => button("Добавить Капучино")?.click());
    await act(async () => button("Добавить Круассан")?.click());
    const staff = button("Персонал");
    expect(staff).toBeDefined();
    await act(async () => staff?.click());
    const slide = example.querySelector('[aria-roledescription="слайд"]');
    expect(slide?.getAttribute("aria-label")).toContain("2 из 2");
    expect(slide?.textContent).toContain("430");
    expect(slide?.textContent).toContain("Капучино");
    expect(slide?.textContent).toContain("Круассан");
    expect(example.querySelector('[aria-label="Добавить Капучино"]')).toBeNull();
    await act(async () => button("Следующий экран")?.click());
    expect(button("Клиент")?.getAttribute("aria-pressed")).toBe("true");
    expect(example.textContent).toContain("Корзина · 2");
    expect(example.textContent).toContain("Оформить · 430");
  });
  it("uses the selected appointment time in the staff view", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const click = async (name: string) => {
      const button = Array.from(example.querySelectorAll("button")).find(el => el.textContent === name);
      expect(button).toBeDefined();
      await act(async () => button?.click());
    };
    await click("Услуги");
    await click("16:30");
    await click("Персонал");
    expect(example.querySelector('[aria-roledescription="слайд"]')?.textContent).toContain("16:30");
    expect(example.textContent).not.toContain("Капучино");
    await click("Клиент");
    expect(example.textContent).toContain("Записаться на 16:30");
  });
  it("keeps the same participant count after joining and switching roles", async () => {
    const example = host.querySelector('[data-testid="landing-example"]')!;
    const click = async (name: string) => {
      const button = Array.from(example.querySelectorAll("button")).find(el => el.textContent === name);
      expect(button).toBeDefined();
      await act(async () => button?.click());
    };
    await click("Клуб");
    await click("Участвовать");
    await click("Персонал");
    expect(example.querySelector('[aria-roledescription="слайд"]')?.textContent).toContain("15 чел.");
    await click("Клиент");
    expect(example.textContent).toContain("идут 15 человек");
    expect(example.textContent).toContain("Вы записаны");
  });
  it("shows plant orders in the shop staff view and preserves an authored prompt", async () => {
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
    await click("Персонал");
    const slide = example.querySelector('[aria-roledescription="слайд"]');
    expect(slide?.textContent).toContain("Фикус");
    expect(slide?.textContent).toContain("980");
    expect(slide?.textContent).not.toContain("Капучино");
    expect(example.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe("Приложение моей пекарни");
  });
  it("describes generation and edits without promising customer version restoration", () => {
    expect(host.textContent).toContain("Правки словами");
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
