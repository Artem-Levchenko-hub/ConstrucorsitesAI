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
  it("keeps every section navigation target on the page", () => {
    for (const link of host.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
      expect(host.querySelector(link.getAttribute("href")!)).not.toBeNull();
    }
  });
});
