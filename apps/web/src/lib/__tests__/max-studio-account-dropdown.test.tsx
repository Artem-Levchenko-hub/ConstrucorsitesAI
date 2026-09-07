import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxStudioHeader } from "@/components/max/MaxStudioHeader";

vi.mock("@/app/(auth)/actions", () => ({ logoutAction: vi.fn() }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const studioCss = readFileSync(
  resolve(process.cwd(), "src/components/max/max-studio.css"),
  "utf8",
);
const accountDropdownPath = resolve(
  process.cwd(),
  "src/components/max/max-account-dropdown.css",
);

function cssForDom() {
  const accountDropdownCss = existsSync(accountDropdownPath)
    ? readFileSync(accountDropdownPath, "utf8")
    : "";

  return `${accountDropdownCss}\n${studioCss}`
    .replaceAll(":focus-visible", ".is-keyboard-focus")
    .replaceAll("var(--color-surface-3)", "#eef2f7")
    .replaceAll("var(--color-accent)", "#2563eb");
}

describe("MAX Studio account dropdown", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    document.head.replaceChildren();
    document.body.replaceChildren();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);

    await act(async () => {
      root.render(<MaxStudioHeader email="owner@example.test" />);
    });
    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('[aria-label="Аккаунт"]')!
        .dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
  });

  it("uses one direct interactive menu-item surface for links and logout", () => {
    const items = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];

    expect(items).toHaveLength(8);
    expect(items.slice(0, -1).every((item) => item.tagName === "A")).toBe(true);
    expect(items.at(-1)?.tagName).toBe("BUTTON");
    expect(items.every((item) => item.querySelector("button, a") === null)).toBe(true);
  });

  it("keeps pointer highlight stable and adds an inset keyboard-focus cue", () => {
    const style = document.createElement("style");
    style.textContent = cssForDom();
    document.head.append(style);

    const menu = document.querySelector<HTMLElement>('[role="menu"]')!;
    const link = menu.querySelector<HTMLElement>('[role="menuitem"]')!;
    const logout = [...menu.querySelectorAll<HTMLElement>('[role="menuitem"]')].at(-1)!;

    expect(menu.classList.contains("max-account-dropdown")).toBe(true);

    for (const item of [link, logout]) {
      item.setAttribute("data-highlighted", "");
      const pointerStyle = getComputedStyle(item);
      expect(pointerStyle.backgroundColor).toBe("rgb(238, 242, 247)");
      expect(pointerStyle.outlineStyle).toBe("none");
      expect(pointerStyle.boxShadow).toBe("none");
      expect(pointerStyle.transition).toBe("none");

      item.classList.add("is-keyboard-focus");
      const keyboardStyle = getComputedStyle(item);
      expect(keyboardStyle.outlineStyle).toBe("none");
      expect(keyboardStyle.boxShadow).toBe("inset 0 0 0 2px #2563eb");
    }
  });
});
