import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MotionLab } from "./MotionLab";

let reduced = false;
let root: Root;
let host: HTMLDivElement;
const media = new EventTarget();
Object.defineProperty(media, "matches", { get: () => reduced });

beforeEach(() => {
  reduced = false;
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("matchMedia", () => media);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("responds to OS motion changes while open, preserving the user's selected state", async () => {
  await act(async () => root.render(<MotionLab />));
  const lab = host.querySelector(".uk-lab")!;
  const toggle = host.querySelector<HTMLButtonElement>('[role="switch"]')!;
  expect(lab.getAttribute("data-reduced")).toBe("false");
  await act(async () => toggle.click());
  expect(toggle.getAttribute("aria-checked")).toBe("false");

  await act(async () => { reduced = true; media.dispatchEvent(new Event("change")); });
  expect(lab.getAttribute("data-reduced")).toBe("true");
  expect(host.textContent).toContain("В системе включено уменьшение движения");
  expect(toggle.getAttribute("aria-checked")).toBe("false");

  const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
  await act(async () => checkbox.click());
  await act(async () => checkbox.click());
  expect(lab.getAttribute("data-reduced")).toBe("true");
  await act(async () => { reduced = false; media.dispatchEvent(new Event("change")); });
  expect(lab.getAttribute("data-reduced")).toBe("false");
  expect(toggle.getAttribute("aria-checked")).toBe("false");
});

it("keeps independent demo states when slowing down or reducing motion", async () => {
  await act(async () => root.render(<MotionLab />));
  const button = (name: string) => [...host.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent === name)!;
  await act(async () => button("Добавить строку").click());
  await act(async () => button("Добавить строку").click());
  await act(async () => button("Поменять порядок").click());
  await act(async () => host.querySelector<HTMLButtonElement>('[role="checkbox"]')!.click());
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Следующий пример шага"]')!.click());
  await act(async () => button("Следующий этап").click());
  const [reduce, slow] = host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
  await act(async () => slow.click());
  expect(host.querySelector(".uk-lab")!.getAttribute("data-slow")).toBe("true");
  await act(async () => reduce.click());
  expect(slow.disabled).toBe(true);
  expect(host.querySelector('[role="checkbox"]')!.getAttribute("aria-checked")).toBe("true");
  expect(host.querySelector('[role="progressbar"]')!.getAttribute("aria-valuenow")).toBe("50");
  expect(host.querySelector(".uk-motion-list li")!.textContent).toBe("Клуб клиентов");
  expect(host.textContent).toContain("Шаг 2 из 3");
});

it("dismisses a keyboard tooltip with Escape while preserving focus", async () => {
  await act(async () => root.render(<MotionLab />));
  const trigger = host.querySelector<HTMLButtonElement>(".uk-help-button")!;
  await act(async () => trigger.focus());
  expect(trigger.getAttribute("aria-describedby")).toBe(host.querySelector('[role="tooltip"]')!.id);
  await act(async () => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(trigger.hasAttribute("aria-describedby")).toBe(false);
  expect(document.activeElement).toBe(trigger);
});
