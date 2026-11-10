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
