import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountPageTransition } from "@/components/account/AccountPageTransition";

let reduced = false;
const media = new EventTarget();
Object.defineProperty(media, "matches", { get: () => reduced });
Object.assign(media, {
  addListener: (listener: EventListener) => media.addEventListener("change", listener),
  removeListener: (listener: EventListener) => media.removeEventListener("change", listener),
});
function setReduced(value: boolean) { reduced = value; media.dispatchEvent(new Event("change")); }

let root: Root;
let host: HTMLDivElement;
let mounts: number;
let unmounts: number;
const cancel = vi.fn();
const animate = vi.fn(() => ({ cancel }));
const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");

function Draft() {
  const [count, setCount] = useState(0);
  useEffect(() => { mounts += 1; return () => { unmounts += 1; }; }, []);
  return <><textarea aria-label="Черновик" defaultValue="Мой ответ" /><button onClick={() => setCount(value => value + 1)}>Правок: {count}</button></>;
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  setReduced(false); mounts = 0; unmounts = 0;
  animate.mockClear(); cancel.mockClear();
  Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, value: animate });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove();
  if (originalAnimate) Object.defineProperty(HTMLElement.prototype, "animate", originalAnimate);
  else Reflect.deleteProperty(HTMLElement.prototype, "animate");
  vi.unstubAllGlobals();
});
async function render(key: string, allowed = true) {
  await act(async () => root.render(<AccountPageTransition transitionKey={key}>{allowed ? <Draft /> : <p>Нет доступа</p>}</AccountPageTransition>));
}

it("animates a transition while keeping the draft, component state and keyboard focus", async () => {
  await render("support");
  const textarea = host.querySelector("textarea")!;
  textarea.value = "Несохранённый ответ";
  await act(async () => host.querySelector("button")!.click());
  textarea.focus();
  await render("support-details");
  expect(animate).toHaveBeenCalledTimes(1);
  expect(host.querySelector("textarea")).toBe(textarea);
  expect(textarea.value).toBe("Несохранённый ответ");
  expect(document.activeElement).toBe(textarea);
  expect(host.textContent).toContain("Правок: 1");
  expect(mounts).toBe(1); expect(unmounts).toBe(0);
});

it("removes private content immediately during a transition", async () => {
  await render("support"); await render("support-details");
  await render("access-revoked", false);
  expect(host.querySelector("textarea")).toBeNull();
  expect(host.textContent).toBe("Нет доступа");
  expect(unmounts).toBe(1);
  expect(cancel).toHaveBeenCalledTimes(1);
});

it("does not start movement when reduced motion is already enabled", async () => {
  setReduced(true);
  await render("billing"); await render("transactions");
  expect(animate).not.toHaveBeenCalled();
});

it("cancels movement and prevents later movement after the live media preference changes", async () => {
  await render("billing"); await render("transactions");
  expect(animate).toHaveBeenCalledTimes(1);
  await act(async () => setReduced(true));
  await render("transactions");
  expect(cancel).toHaveBeenCalledTimes(1);
  await render("plan");
  expect(animate).toHaveBeenCalledTimes(1);
  expect(host.querySelector("textarea")).not.toBeNull();
});

it("keeps content usable in browsers without the animation API", async () => {
  Reflect.deleteProperty(HTMLElement.prototype, "animate");
  await render("billing"); await render("transactions");
  expect(host.textContent).toContain("Правок: 0");
  expect(mounts).toBe(1); expect(unmounts).toBe(0);
});

it("keeps content usable and still when motion preferences cannot be queried", async () => {
  vi.stubGlobal("matchMedia", undefined);
  await render("billing"); await render("transactions");
  expect(animate).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Правок: 0");
  expect(mounts).toBe(1);
});
